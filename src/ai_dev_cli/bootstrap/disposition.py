"""Disposition CSV production and parsing."""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_dev_cli.bootstrap import profiles, sidecar

Column = Literal["path", "target", "disp", "action", "why", "next"]
COLUMNS: tuple[Column, ...] = ("path", "target", "disp", "action", "why", "next")
ALLOWED_DISPOSITIONS = frozenset({"add", "keep", "divergent", "cheap", "medium", "expensive", "drop"})
ALLOWED_ACTIONS = frozenset({"add", "keep", "move", "drop"})
DROP_PATTERNS = (
    "def pytest_pyfunc_call",
    "def pytest_sessionfinish",
    "Field(default=[]",
    "Field(default={}",
    "Field(default=set()",
    'Path(".")',
)
EXPENSIVE_PATTERNS = (
    "AsyncOpenAI(",
    "OpenAI(",
    "Conversation(",
    ".send(",
    "RUN_BENCHMARK",
    "benchmark",
    "live",
)
MEDIUM_PATTERNS = ("pytest.fixture", "monkeypatch", "tmp_path", "fixture")


class DispositionError(Exception):
    """CSV parse or validation error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DispositionRow:
    """One disposition CSV row."""

    path: str
    target: str
    disp: str
    action: str
    why: str
    next: str


@dataclass(frozen=True, slots=True)
class Plan:
    """Generated upgrade plan rows and blocking paths."""

    rows: tuple[DispositionRow, ...]
    blocks: tuple[str, ...]


def build_plan(root: Path, profile: profiles.Profile, managed: sidecar.Sidecar | None) -> Plan:
    """Build a stable upgrade plan for profile targets and tests."""
    profile_rows = _profile_rows(root, profile, managed)
    test_rows = _test_rows(root, profile)
    rows = (*profile_rows, *test_rows)
    blocks = _blocking_paths(root, managed, rows)
    return Plan(rows=rows, blocks=blocks)


def write_csv(path: Path, rows: tuple[DispositionRow, ...]) -> None:
    """Write disposition rows as CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(_row_dict(row))


def parse_csv(path: Path) -> tuple[DispositionRow, ...]:
    """Parse and validate disposition CSV rows."""
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise DispositionError(
                "BLOCKED: disposition CSV has invalid columns. Use path,target,disp,action,why,next instead."
            )
        rows = tuple(_parse_row(index, raw) for index, raw in enumerate(reader, 2))

    seen: set[str] = set()
    for row in rows:
        if row.path in seen:
            raise DispositionError(
                f"BLOCKED: disposition CSV has duplicate path '{row.path}'. Use one row per path instead."
            )
        seen.add(row.path)
    return rows


def _profile_rows(
    root: Path,
    profile: profiles.Profile,
    managed: sidecar.Sidecar | None,
) -> tuple[DispositionRow, ...]:
    rows: list[DispositionRow] = []
    for target in sorted(profiles.expected_targets(profile)):
        state = sidecar.classify_path(root, managed, target)
        if state.state == "absent":
            rows.append(DispositionRow(target, target, "add", "add", "profile target missing", "write template"))
        elif state.state == "managed-clean":
            rows.append(DispositionRow(target, target, "keep", "keep", "managed profile target unchanged", "keep"))
        elif state.state == "managed-divergent":
            rows.append(
                DispositionRow(
                    target,
                    target,
                    "divergent",
                    "keep",
                    "managed file edited outside ai-dev-cli",
                    "review git diff",
                )
            )
        else:
            rows.append(
                DispositionRow(
                    target,
                    target,
                    "divergent",
                    "keep",
                    "unmanaged profile target would be overwritten",
                    "review and accept",
                )
            )
    return tuple(rows)


def _test_rows(root: Path, profile: profiles.Profile) -> tuple[DispositionRow, ...]:
    roots = _test_roots(profile)
    files: list[Path] = []
    for test_root in roots:
        path = root / test_root
        if path.is_dir():
            files.extend(item for item in sorted(path.rglob("test_*.py")) if item.is_file())
        elif path.is_file() and path.name.startswith("test_") and path.suffix == ".py":
            files.append(path)

    rows = []
    for file_path in sorted(set(files)):
        relative = file_path.relative_to(root).as_posix()
        target = _target_for_test(relative)
        disp, action, why, next_step = _classify_test(file_path, relative, target)
        rows.append(DispositionRow(relative, target, disp, action, why, next_step))
    return tuple(rows)


def _blocking_paths(
    root: Path,
    managed: sidecar.Sidecar | None,
    rows: tuple[DispositionRow, ...],
) -> tuple[str, ...]:
    blocked: list[str] = []
    for row in rows:
        if row.disp == "divergent":
            blocked.append(row.path)
        if row.action == "move" and row.target:
            target_state = sidecar.classify_path(root, managed, row.target)
            if target_state.state != "absent":
                blocked.append(row.target)
    return tuple(dict.fromkeys(blocked))


def _test_roots(profile: profiles.Profile) -> tuple[str, ...]:
    raw_roots = profile.tool_dev_cli.get("test_roots", ("tests",))
    roots = tuple(item for item in raw_roots if isinstance(item, str)) if isinstance(raw_roots, list | tuple) else ()
    examples = profile.tool_dev_cli.get("examples", {})
    benchmark = profile.tool_dev_cli.get("benchmark", {})
    extras = []
    if isinstance(examples, dict):
        extras.extend(value for key, value in examples.items() if key.endswith("_path") and isinstance(value, str))
    if isinstance(benchmark, dict) and isinstance(benchmark.get("target"), str):
        extras.append(benchmark["target"])
    return tuple(dict.fromkeys((*roots, *extras)))


def _target_for_test(path: str) -> str:
    if path == "tests/test_smoke.py":
        return "examples/tests/test_smoke.py"
    if path == "tests/test_examples_static.py":
        return "examples/tests/test_static.py"
    return path


def _classify_test(file_path: Path, relative: str, target: str) -> tuple[str, str, str, str]:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    if any(pattern in text for pattern in DROP_PATTERNS):
        return "drop", "drop", "irreparable test authoring pattern", "drop"
    if target != relative:
        return "cheap", "move", "profile-layout correction", "move"
    if _expensive(relative, text):
        return "expensive", "keep", "live or expensive behavior needs maintainer verification", "leave"
    if any(pattern in text for pattern in MEDIUM_PATTERNS):
        return "medium", "keep", "isolated fixture or small lane decision", "manual review"
    return "cheap", "keep", "pure unit test", "keep"


def _expensive(relative: str, text: str) -> bool:
    return relative.startswith(("tests/integration/", "tests/qualification/", "benchmarks/")) or any(
        pattern in text for pattern in EXPENSIVE_PATTERNS
    )


def _parse_row(index: int, raw: dict[str, str | None]) -> DispositionRow:
    values = {column: (raw.get(column) or "") for column in COLUMNS}
    if not values["path"]:
        raise DispositionError(f"BLOCKED: disposition CSV row {index} is missing path. Use a path instead.")
    if values["disp"] not in ALLOWED_DISPOSITIONS:
        raise DispositionError(
            f"BLOCKED: disposition CSV row {index} has invalid disposition '{values['disp']}'. "
            "Use a known disposition instead."
        )
    if values["action"] not in ALLOWED_ACTIONS:
        raise DispositionError(
            f"BLOCKED: disposition CSV row {index} has invalid action '{values['action']}'. "
            "Use keep, add, move, or drop instead."
        )
    if values["action"] == "move" and not values["target"]:
        raise DispositionError(f"BLOCKED: disposition CSV row {index} has no move target. Use a target instead.")
    return DispositionRow(**values)


def _row_dict(row: DispositionRow) -> dict[Column, str]:
    return {
        "path": row.path,
        "target": row.target,
        "disp": row.disp,
        "action": row.action,
        "why": row.why,
        "next": row.next,
    }
