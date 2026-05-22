"""Universal AST validator for test authoring policies."""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_dev_cli._lane import load_lanes
from ai_dev_cli._project import load_config

TOKEN_CAP_KEYWORDS = frozenset({"max_completion_tokens", "max_output_tokens", "max_tokens"})
PYTEST_RUNNER_HOOKS = frozenset({"pytest_pyfunc_call", "pytest_sessionfinish"})
OPENAI_CLIENT_NAMES = frozenset({"AsyncOpenAI", "OpenAI"})
OPENAI_CHAIN_SUFFIXES = ("chat.completions.create", "responses.create", "completions.create")


@dataclass(frozen=True, slots=True)
class Violation:
    """One validator violation."""

    path: Path
    line: int
    rule_id: str
    message: str

    def format(self, root: Path) -> str:
        return f"{self.path.relative_to(root).as_posix()}:{self.line}: VALIDATOR-{self.rule_id}: {self.message}"


def _read_tree(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        line = exc.lineno or 1
        raise ValueError(f"{path}:{line}: syntax error: {exc.msg}") from exc


def _is_under(root: Path, path: Path, relative_prefix: str) -> bool:
    try:
        path.relative_to(root / relative_prefix)
    except ValueError:
        return False
    return True


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_test_files(root: Path, test_file_roots: tuple[str, ...]) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative_root in test_file_roots:
        test_path = root / relative_root
        if test_path.is_file() and test_path.suffix == ".py":
            files.append(test_path)
        elif test_path.is_dir():
            files.extend(path for path in sorted(test_path.rglob("*.py")) if path.is_file())
    return tuple(files)


def _module_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _module_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _module_name(node.func)
    return ""


def _literal_number(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _literal_number(node.operand)
        return -value if value is not None else None
    return None


def _module_constants(tree: ast.Module) -> dict[str, float]:
    constants: dict[str, float] = {}
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        value = _literal_number(stmt.value)
        if value is None:
            continue
        for target in stmt.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    return constants


def _sleep_argument_value(arg: ast.AST, constants: dict[str, float]) -> float | None:
    literal = _literal_number(arg)
    if literal is not None:
        return literal
    if isinstance(arg, ast.Name):
        return constants.get(arg.id)
    return None


def _is_asyncio_sleep(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "sleep"
        and _module_name(call.func.value) == "asyncio"
    )


def _check_sleep_constants(
    root: Path,
    path: Path,
    tree: ast.Module,
    qualification_roots: tuple[str, ...],
) -> list[Violation]:
    if any(_is_under(root, path, qualification_root) for qualification_root in qualification_roots):
        return []

    constants = _module_constants(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_asyncio_sleep(node) or not node.args:
            continue
        resolved = _sleep_argument_value(node.args[0], constants)
        if resolved is not None and resolved >= 1:
            violations.append(
                Violation(
                    path=path,
                    line=getattr(node, "lineno", 1),
                    rule_id="ASYNC-SLEEP",
                    message="asyncio.sleep() >= 1 second is allowed only in tests/qualification/",
                )
            )
    return violations


def _check_path_dot(path: Path, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _module_name(node.func) != "Path" or not node.args:
            continue
        if isinstance(node.args[0], ast.Constant) and node.args[0].value == ".":
            violations.append(
                Violation(
                    path=path,
                    line=getattr(node, "lineno", 1),
                    rule_id="PATH-DOT",
                    message="Path('.') is xdist-unsafe in tests; use tmp_path or an explicit project path",
                )
            )
    return violations


def _check_token_caps(path: Path, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        violations.extend(
            Violation(
                path=path,
                line=getattr(keyword.value, "lineno", getattr(node, "lineno", 1)),
                rule_id="TOKEN-CAP",
                message="token cap keyword arguments are forbidden in tests",
            )
            for keyword in node.keywords
            if keyword.arg in TOKEN_CAP_KEYWORDS
        )
    return violations


def _check_pytest_runner_hooks(path: Path, tree: ast.Module) -> list[Violation]:
    return [
        Violation(
            path=path,
            line=getattr(node, "lineno", 1),
            rule_id="PYTEST-HOOK",
            message=f"custom {node.name} overrides are forbidden; use pytest-asyncio and dev lane policy",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in PYTEST_RUNNER_HOOKS
    ]


def _check_stacked_parametrize(path: Path, tree: ast.Module) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        count = sum(1 for decorator in node.decorator_list if _module_name(decorator) == "pytest.mark.parametrize")
        if count > 1:
            violations.append(
                Violation(
                    path=path,
                    line=getattr(node, "lineno", 1),
                    rule_id="STACKED-PARAMETRIZE",
                    message="stacked parametrize is forbidden; use one axis, separate tests, or qualification",
                )
            )
    return violations


def _is_live_llm_call(call: ast.Call) -> bool:
    parts = _module_name(call.func).split(".")
    for index, part in enumerate(parts):
        if part in OPENAI_CLIENT_NAMES and ".".join(parts[index + 1 :]) in OPENAI_CHAIN_SUFFIXES:
            return True
    return False


def _check_live_llm_in_unit(root: Path, path: Path, tree: ast.Module, unit_roots: tuple[str, ...]) -> list[Violation]:
    if not any(_is_under(root, path, unit_root) for unit_root in unit_roots):
        return []

    violations: list[Violation] = []
    violations.extend(
        Violation(
            path=path,
            line=getattr(node, "lineno", 1),
            rule_id="LIVE-LLM-UNIT",
            message="unit tests must not call live LLM APIs; patch the provider boundary or move lanes",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_live_llm_call(node)
    )
    return violations


def _configured_path_values(section: Any, keys: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(section, dict):
        return ()
    return tuple(value for key in keys if isinstance((value := section.get(key)), str))


def _dedupe(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(path.rstrip("/") for path in paths if path))


def _resolved_test_file_roots(test_roots: list[str], cli_config: dict[str, Any]) -> tuple[str, ...]:
    examples = _configured_path_values(cli_config.get("examples"), ("static_path", "smoke_path", "live_path"))
    benchmark = _configured_path_values(cli_config.get("benchmark"), ("target",))
    return _dedupe((*test_roots, *examples, *benchmark))


def _lane_roots(name: str) -> tuple[str, ...]:
    lane = load_lanes().get(name)
    return lane.paths if lane is not None else ()


def validate(
    root: Path,
    test_file_roots: tuple[str, ...],
    unit_roots: tuple[str, ...],
    qualification_roots: tuple[str, ...],
) -> list[Violation]:
    """Run universal test authoring checks."""
    violations: list[Violation] = []
    for path in _iter_test_files(root, test_file_roots):
        tree = _read_tree(path)
        violations.extend(_check_sleep_constants(root, path, tree, qualification_roots))
        violations.extend(_check_path_dot(path, tree))
        violations.extend(_check_token_caps(path, tree))
        violations.extend(_check_pytest_runner_hooks(path, tree))
        violations.extend(_check_stacked_parametrize(path, tree))
        violations.extend(_check_live_llm_in_unit(root, path, tree, unit_roots))
    return sorted(violations, key=lambda item: (_relative_path(root, item.path), item.line, item.rule_id))


def main() -> int:
    """Validate universal test authoring rules."""
    cfg = load_config()
    root = cfg.repo_root
    test_file_roots = _resolved_test_file_roots(cfg.test_roots, cfg.dev_cli_config)
    try:
        violations = validate(
            root,
            test_file_roots,
            unit_roots=_lane_roots("unit"),
            qualification_roots=_lane_roots("qualification"),
        )
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.stderr.write("Next step: fix the syntax error and rerun dev check.\n")
        return 1

    for violation in violations:
        sys.stdout.write(f"{violation.format(root)}\n")
    if violations:
        sys.stdout.write("Next step: fix the validator violations and rerun dev check.\n")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
