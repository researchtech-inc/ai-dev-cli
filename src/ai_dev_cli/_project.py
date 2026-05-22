"""Project-agnostic configuration via auto-detection and optional pyproject.toml overrides."""

import hashlib
import json
import shlex
import shutil
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ai_dev_cli._imports import scan_test_imports
from ai_dev_cli._infra import InfraCheck, parse_infrastructure


@dataclass(frozen=True, slots=True)
class CheckStep:
    """A single CI check step."""

    name: str
    description: str
    commands: tuple[tuple[str, ...], ...]
    cacheable: bool = False


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Fully resolved project configuration. Immutable after creation."""

    repo_root: Path
    runs_dir: Path
    state_file: Path
    dev_cli_config: dict[str, Any]

    source_to_test: dict[str, list[str]]
    scope_aliases: dict[str, str]
    markers: dict[str, str]
    test_groups: dict[str, str]
    checks: tuple[CheckStep, ...]
    tracked_extensions: frozenset[str]
    runner_prefix: tuple[str, ...]
    test_roots: list[str]
    infrastructure: tuple[InfraCheck, ...]
    source_packages: list[str]
    coverage_sources: list[str]
    coverage_fail_under: int | None
    hash_globs: tuple[str, ...]
    hash_files: tuple[str, ...]
    hash_optional_files: tuple[str, ...]
    step_timeouts: dict[str, int]
    config_hash: str

    has_ruff: bool
    has_basedpyright: bool
    has_pyright: bool
    has_mypy: bool
    has_vulture: bool
    has_interrogate: bool
    has_semgrep: bool
    has_make: bool

    def command(self, executable: str, *args: str) -> tuple[str, ...]:
        """Build a command tuple using the detected runner prefix."""
        return (*self.runner_prefix, executable, *args)


_ROOT_MARKERS = ("pyproject.toml", ".git", "setup.cfg", "setup.py", "tox.ini")
STEP_TIMEOUT_DEFAULTS: dict[str, int] = {
    "lint": 300,
    "typecheck": 300,
    "deadcode": 300,
    "semgrep": 600,
    "docstrings": 300,
    "test-validator": 120,
}


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default: cwd) to find the project root."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for marker in _ROOT_MARKERS:
            if (directory / marker).exists():
                return directory
    return current


def _has_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _detect_runner_prefix(root: Path, cli_config: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Detect how to run Python tools."""
    runner: tuple[str, ...] = ()
    if cli_config:
        explicit = cli_config.get("runner")
        if explicit == "none":
            runner = ()
        elif explicit == "uv":
            runner = ("uv", "run")
        elif explicit == "poetry":
            runner = ("poetry", "run")
    if runner or _has_tool("pytest"):
        return runner
    if (root / "uv.lock").exists() and _has_tool("uv"):
        runner = ("uv", "run")
    elif (root / "poetry.lock").exists() and _has_tool("poetry"):
        runner = ("poetry", "run")
    return runner


def _read_pyproject(root: Path) -> dict[str, Any]:
    path = root / "pyproject.toml"
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _read_dev_cli_config(pyproject: dict[str, Any]) -> dict[str, Any]:
    config = pyproject.get("tool", {}).get("dev-cli", {})
    return config if isinstance(config, dict) else {}


def _read_pytest_config(pyproject: dict[str, Any]) -> dict[str, Any]:
    config = pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return config if isinstance(config, dict) else {}


def _parse_markers(pytest_config: dict[str, Any]) -> dict[str, str]:
    """Parse pytest markers into {name: description}."""
    result: dict[str, str] = {}
    for entry in pytest_config.get("markers", []):
        if not isinstance(entry, str):
            continue
        if ":" in entry:
            name, desc = entry.split(":", 1)
            result[name.strip()] = desc.strip()
        else:
            result[entry.strip()] = ""
    return result


def _read_testpaths(pytest_config: dict[str, Any]) -> list[str]:
    raw = pytest_config.get("testpaths", ["tests"])
    return [item for item in raw if isinstance(item, str)] if isinstance(raw, list | tuple) else ["tests"]


def _extract_marker_from_addopts(addopts: str) -> str:
    """Extract -m expression from an addopts string."""
    try:
        tokens = shlex.split(addopts)
    except ValueError:
        tokens = addopts.split()
    for index, argument in enumerate(tokens):
        if argument == "-m" and index + 1 < len(tokens):
            return tokens[index + 1]
    return ""


_EXCLUDED_TOP_DIRS = frozenset({
    "tests",
    "test",
    "docs",
    "doc",
    "build",
    "dist",
    ".venv",
    "venv",
    "env",
    ".git",
    ".tmp",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "scripts",
    "examples",
    "tools",
    ".github",
})


def _discover_source_packages(root: Path) -> list[str]:
    """Find Python source packages at the top level or under src/."""
    candidates: list[str] = []

    src = root / "src"
    if src.is_dir():
        candidates.extend(
            str(child.relative_to(root))
            for child in sorted(src.iterdir())
            if child.is_dir() and (child / "__init__.py").exists()
        )

    candidates.extend(
        child.name
        for child in sorted(root.iterdir())
        if (
            child.is_dir()
            and child.name not in _EXCLUDED_TOP_DIRS
            and not child.name.startswith(".")
            and (child / "__init__.py").exists()
        )
    )

    return candidates


def _discover_test_subdirs(root: Path, test_roots: list[str]) -> list[str]:
    """Find all test subdirectories under each test root."""
    result: list[str] = []
    for test_root in test_roots:
        test_path = root / test_root
        if not test_path.is_dir():
            continue
        result.extend(
            str(child.relative_to(root))
            for child in sorted(test_path.iterdir())
            if child.is_dir() and not child.name.startswith(".") and child.name not in {"__pycache__", "test_data"}
        )
    return result


def _build_source_to_test_map(
    root: Path,
    source_packages: list[str],
    test_roots: list[str],
) -> dict[str, list[str]]:
    """Build source-to-test mapping by scanning actual imports in test files."""
    mapping = scan_test_imports(root, test_roots, source_packages)
    if mapping:
        return mapping

    test_subdirs = _discover_test_subdirs(root, test_roots)
    test_by_leaf: dict[str, list[str]] = {}
    for td in test_subdirs:
        leaf = Path(td).name
        test_by_leaf.setdefault(leaf, []).append(td)

    fallback: dict[str, list[str]] = {}
    for pkg in source_packages:
        pkg_path = root / pkg
        if not pkg_path.is_dir():
            continue
        for child in sorted(pkg_path.iterdir()):
            if not child.is_dir() or child.name == "__pycache__":
                continue
            source_key = str(child.relative_to(root))
            matched = test_by_leaf.get(child.name, [])
            if not matched and child.name.startswith("_"):
                matched = test_by_leaf.get(child.name.lstrip("_"), [])
            if matched:
                fallback[source_key] = list(matched)

    return fallback


def _build_scope_aliases(test_subdirs: list[str]) -> dict[str, str]:
    """Generate scope aliases from test subdirectory names."""
    aliases: dict[str, str] = {}
    for td in test_subdirs:
        leaf = Path(td).name
        if not leaf.startswith("_"):
            aliases[leaf] = td
    return aliases


_HEAVY_MARKER_NAMES = frozenset({
    "integration",
    "clickhouse",
    "pubsub",
    "pubsub_live",
    "slow",
    "e2e",
})


def _build_test_groups(markers: dict[str, str], pytest_config: dict[str, Any]) -> dict[str, str]:
    """Build test group marker expressions from project markers."""
    addopts = pytest_config.get("addopts", "")
    addopts_marker = _extract_marker_from_addopts(addopts) if isinstance(addopts, str) else ""

    heavy_in_project = sorted(m for m in markers if m in _HEAVY_MARKER_NAMES)

    if addopts_marker:
        unit_expr = addopts_marker
    elif heavy_in_project:
        unit_expr = " and ".join(f"not {m}" for m in heavy_in_project)
    else:
        unit_expr = ""

    groups: dict[str, str] = {"unit": unit_expr, "all": ""}
    groups.update({marker: marker for marker in heavy_in_project})
    return groups


def _build_check_steps(
    cfg_partial: _PartialConfig,
    root: Path,
    source_packages: list[str],
    cli_config: dict[str, Any],
) -> tuple[CheckStep, ...]:
    """Build CI check steps from detected tools. Fast checks first, slow last."""
    steps: list[CheckStep] = []
    r = cfg_partial.runner_prefix

    if cfg_partial.has_ruff:
        steps.append(
            CheckStep(
                "lint",
                "Ruff check + format",
                (
                    (*r, "ruff", "check", "."),
                    (*r, "ruff", "format", "--check", "."),
                ),
                cacheable=True,
            )
        )

    if cfg_partial.has_basedpyright:
        cmds: list[tuple[str, ...]] = [(*r, "basedpyright", "--level", "warning")]
        if (root / "pyrightconfig.tests.json").exists():
            cmds.append((*r, "basedpyright", "--level", "error", "-p", "pyrightconfig.tests.json"))
        steps.append(CheckStep("typecheck", "BasedPyright", tuple(cmds), cacheable=True))
    elif cfg_partial.has_pyright:
        steps.append(CheckStep("typecheck", "Pyright", ((*r, "pyright"),), cacheable=True))
    elif cfg_partial.has_mypy:
        steps.append(CheckStep("typecheck", "Mypy", ((*r, "mypy", "."),), cacheable=True))

    if cfg_partial.has_vulture:
        vulture_cmd = [*r, "vulture"]
        vulture_cmd.extend(f"{package.rstrip('/')}/" for package in source_packages)
        if (root / ".vulture_whitelist.py").exists():
            vulture_cmd.append(".vulture_whitelist.py")
        vulture_cmd.extend(["--min-confidence", "80"])
        steps.append(CheckStep("deadcode", "Vulture dead code", (tuple(vulture_cmd),), cacheable=True))

    if (root / ".semgrep").is_dir():
        semgrep_cmd = (*r, "semgrep", "--config", ".semgrep/", ".", "--error", "--quiet")
        if not cfg_partial.has_semgrep:
            semgrep_cmd = ("uvx", "semgrep", "--config", ".semgrep/", ".", "--error", "--quiet")
        steps.append(CheckStep("semgrep", "Semgrep custom rules", (semgrep_cmd,)))

    if cfg_partial.has_interrogate and source_packages:
        interrogate_targets = [pkg for pkg in source_packages if not pkg.startswith("src/")]
        if not interrogate_targets:
            interrogate_targets = source_packages
        steps.append(
            CheckStep(
                "docstrings",
                "Docstring coverage",
                ((*r, "interrogate", "-v", "--fail-under", "100", *interrogate_targets),),
            )
        )

    steps.append(CheckStep("test", "Tests", (_default_test_command(r, cli_config),)))

    return tuple(steps)


def _default_test_command(runner: tuple[str, ...], cli_config: dict[str, Any]) -> tuple[str, ...]:
    raw_lanes = cli_config.get("lanes")
    if isinstance(raw_lanes, dict):
        lane_names = sorted(
            name for name, spec in raw_lanes.items() if isinstance(name, str) and isinstance(spec, dict)
        )
        if not lane_names:
            return (*runner, "dev", "test")
        lane = "unit" if "unit" in lane_names else lane_names[0]
        return (*runner, "dev", "test", f"--lane={lane}")
    return (*runner, "dev", "test")


def _build_bedrock_check_steps(cfg_partial: _PartialConfig) -> tuple[CheckStep, ...]:
    """Build hard bedrock checks that project config cannot remove."""
    package_root = Path(__file__).parent
    semgrep_rules = package_root / "_bedrock" / "semgrep"
    r = cfg_partial.runner_prefix

    if cfg_partial.has_semgrep:
        semgrep_cmd = (*r, "semgrep", "--config", str(semgrep_rules), ".", "--error", "--quiet")
    else:
        semgrep_cmd = ("uvx", "semgrep", "--config", str(semgrep_rules), ".", "--error", "--quiet")

    return (
        CheckStep("bedrock-semgrep", "Hard bedrock Semgrep rules", (semgrep_cmd,)),
        CheckStep(
            "bedrock-delegation",
            "Quality-surface delegation validator",
            ((*r, "python", "-m", "ai_dev_cli._bedrock.delegation"),),
        ),
        CheckStep(
            "bedrock-test-validator",
            "Universal test authoring validator",
            ((*r, "python", "-m", "ai_dev_cli._test_validator"),),
        ),
    )


def _build_check_step_from_config(entry: Any, runner: tuple[str, ...]) -> tuple[CheckStep, str | None] | None:
    if not isinstance(entry, dict):
        return None

    name = entry.get("name", "")
    if not isinstance(name, str) or not name:
        return None

    desc = entry.get("description", name)
    run_entries = entry.get("run", [])
    if isinstance(run_entries, str | list | tuple):
        run_entries = [run_entries] if isinstance(run_entries, str) else run_entries
    else:
        return None

    commands: list[tuple[str, ...]] = []
    for cmd in run_entries:
        if isinstance(cmd, str):
            commands.append((*runner, *shlex.split(cmd)))
        elif isinstance(cmd, list | tuple) and all(isinstance(part, str) for part in cmd):
            commands.append(tuple(cmd))

    if not commands:
        return None

    position = entry.get("position")
    return (
        CheckStep(
            name=name,
            description=desc if isinstance(desc, str) else name,
            commands=tuple(commands),
            cacheable=bool(entry.get("cacheable", False)),
        ),
        position if isinstance(position, str) else None,
    )


def _position_index(steps: list[CheckStep], position: str | None) -> int | None:
    if position == "first":
        return 0
    if position is None or position == "last":
        return None
    if position.startswith("before:"):
        target = position.removeprefix("before:")
        for index, step in enumerate(steps):
            if step.name == target:
                return index
    if position.startswith("after:"):
        target = position.removeprefix("after:")
        for index, step in enumerate(steps):
            if step.name == target:
                return index + 1
    return None


def _apply_configured_check_steps(
    auto_steps: tuple[CheckStep, ...],
    cli_config: dict[str, Any],
    runner: tuple[str, ...],
) -> tuple[CheckStep, ...]:
    """Add or override check steps from explicit [tool.dev-cli.checks] config."""
    raw_checks = cli_config.get("checks", [])
    if not isinstance(raw_checks, list | tuple):
        return auto_steps

    steps = [] if cli_config.get("replace_auto_checks") else list(auto_steps)
    for raw in raw_checks:
        parsed = _build_check_step_from_config(raw, runner)
        if parsed is None:
            continue
        step, position = parsed
        existing_index = next((index for index, current in enumerate(steps) if current.name == step.name), None)
        if existing_index is not None:
            steps.pop(existing_index)

        index = _position_index(steps, position)
        if index is None:
            if existing_index is not None and existing_index <= len(steps):
                steps.insert(existing_index, step)
            else:
                steps.append(step)
        else:
            steps.insert(index, step)

    return tuple(steps)


def _as_str_tuple(value: Any, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list | tuple):
        return default
    return tuple(item for item in value if isinstance(item, str))


def _read_hash_config(cli_config: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    raw = cli_config.get("hash", {})
    if not isinstance(raw, dict):
        raw = {}
    globs = _as_str_tuple(raw.get("globs"))
    files = _as_str_tuple(raw.get("files"), ("pyproject.toml",))
    optional_files = _as_str_tuple(raw.get("optional_files"))
    return globs, files, optional_files


def _read_step_timeouts(cli_config: dict[str, Any]) -> dict[str, int]:
    raw = cli_config.get("step_timeouts", {})
    configured = raw if isinstance(raw, dict) else {}
    timeouts = dict(STEP_TIMEOUT_DEFAULTS)

    for key, default in STEP_TIMEOUT_DEFAULTS.items():
        toml_key = key.replace("-", "_")
        value = configured.get(toml_key, configured.get(key))
        if isinstance(value, int):
            timeouts[key] = max(default, value)

    return timeouts


@dataclass
class _PartialConfig:
    runner_prefix: tuple[str, ...]
    has_ruff: bool
    has_basedpyright: bool
    has_pyright: bool
    has_mypy: bool
    has_vulture: bool
    has_interrogate: bool
    has_semgrep: bool
    has_make: bool


def _config_hash(
    *,
    cli_config: dict[str, Any],
    cache_dir: str,
    runner: tuple[str, ...],
    test_roots: list[str],
    source_packages: list[str],
    tracked_extensions: frozenset[str],
    hash_globs: tuple[str, ...],
    hash_files: tuple[str, ...],
    hash_optional_files: tuple[str, ...],
    step_timeouts: dict[str, int],
    test_groups: dict[str, str],
    checks: tuple[CheckStep, ...],
) -> str:
    payload = {
        "schema_version": cli_config.get("schema_version", 1),
        "cache_dir": cache_dir,
        "runner_prefix": runner,
        "test_roots": test_roots,
        "source_packages": source_packages,
        "tracked_extensions": sorted(tracked_extensions),
        "hash": {
            "globs": hash_globs,
            "files": hash_files,
            "optional_files": hash_optional_files,
        },
        "step_timeouts": step_timeouts,
        "scopes": cli_config.get("scopes", {}),
        "source_to_test": cli_config.get("source_to_test", {}),
        "test_groups": test_groups,
        "checks": [asdict(step) for step in checks],
        "infrastructure": cli_config.get("infrastructure", {}),
        "lanes": cli_config.get("lanes", {}),
        "examples": cli_config.get("examples", {}),
        "benchmark": cli_config.get("benchmark", {}),
        "probes": cli_config.get("probes", {}),
        "verify": cli_config.get("verify", {}),
        "pytest_plugin": cli_config.get("pytest_plugin", {}),
        "hook": cli_config.get("hook", {}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


_cached_config: ProjectConfig | None = None


def load_config(*, force_reload: bool = False) -> ProjectConfig:
    """Load and cache project configuration."""
    global _cached_config
    if _cached_config is not None and not force_reload:
        return _cached_config

    root = find_repo_root()
    pyproject = _read_pyproject(root)
    cli_config = _read_dev_cli_config(pyproject)
    pytest_config = _read_pytest_config(pyproject)

    cache_dir = cli_config.get("cache_dir", ".tmp/dev-runs")
    cache_dir = cache_dir if isinstance(cache_dir, str) else ".tmp/dev-runs"
    runs_dir = root / cache_dir
    state_file = runs_dir / ".state.json"

    runner = _detect_runner_prefix(root, cli_config)

    has_ruff = _has_tool("ruff")
    has_basedpyright = _has_tool("basedpyright")
    has_pyright = _has_tool("pyright")
    has_mypy = _has_tool("mypy")
    has_vulture = _has_tool("vulture")
    has_interrogate = _has_tool("interrogate")
    has_semgrep = _has_tool("semgrep")
    has_make = _has_tool("make")

    partial = _PartialConfig(
        runner_prefix=runner,
        has_ruff=has_ruff,
        has_basedpyright=has_basedpyright,
        has_pyright=has_pyright,
        has_mypy=has_mypy,
        has_vulture=has_vulture,
        has_interrogate=has_interrogate,
        has_semgrep=has_semgrep,
        has_make=has_make,
    )

    markers = _parse_markers(pytest_config)
    test_roots = list(cli_config.get("test_roots") or _read_testpaths(pytest_config))
    test_subdirs = _discover_test_subdirs(root, test_roots)
    source_packages = list(cli_config.get("source_packages") or _discover_source_packages(root))

    source_to_test = _build_source_to_test_map(root, source_packages, test_roots)
    for src_dir, test_dirs in cli_config.get("source_to_test", {}).items():
        source_to_test[src_dir] = list(test_dirs)

    scope_aliases = _build_scope_aliases(test_subdirs)
    scope_aliases.update(cli_config.get("scopes", {}))

    explicit_groups = cli_config.get("test_groups")
    test_groups = (
        dict(explicit_groups) if isinstance(explicit_groups, dict) else _build_test_groups(markers, pytest_config)
    )

    hard_checks = _build_bedrock_check_steps(partial)
    auto_checks = _build_check_steps(partial, root, source_packages, cli_config)
    checks = (*hard_checks, *_apply_configured_check_steps(auto_checks, cli_config, runner))

    tracked = frozenset(_as_str_tuple(cli_config.get("tracked_extensions"), (".py",)))
    hash_globs, hash_files, hash_optional_files = _read_hash_config(cli_config)
    step_timeouts = _read_step_timeouts(cli_config)

    infrastructure = parse_infrastructure(cli_config)

    cov_config = pyproject.get("tool", {}).get("coverage", {})
    coverage_sources = cov_config.get("run", {}).get("source", source_packages)
    coverage_fail_under_raw = cov_config.get("report", {}).get("fail_under")
    coverage_fail_under = int(coverage_fail_under_raw) if coverage_fail_under_raw is not None else None

    config_digest = _config_hash(
        cli_config=cli_config,
        cache_dir=cache_dir,
        runner=runner,
        test_roots=test_roots,
        source_packages=source_packages,
        tracked_extensions=tracked,
        hash_globs=hash_globs,
        hash_files=hash_files,
        hash_optional_files=hash_optional_files,
        step_timeouts=step_timeouts,
        test_groups=test_groups,
        checks=checks,
    )

    config = ProjectConfig(
        repo_root=root,
        runs_dir=runs_dir,
        state_file=state_file,
        dev_cli_config=cli_config,
        source_to_test=source_to_test,
        scope_aliases=scope_aliases,
        markers=markers,
        test_groups=test_groups,
        checks=checks,
        tracked_extensions=tracked,
        runner_prefix=runner,
        test_roots=test_roots,
        infrastructure=infrastructure,
        source_packages=source_packages,
        coverage_sources=coverage_sources,
        coverage_fail_under=coverage_fail_under,
        hash_globs=hash_globs,
        hash_files=hash_files,
        hash_optional_files=hash_optional_files,
        step_timeouts=step_timeouts,
        config_hash=config_digest,
        has_ruff=has_ruff,
        has_basedpyright=has_basedpyright,
        has_pyright=has_pyright,
        has_mypy=has_mypy,
        has_vulture=has_vulture,
        has_interrogate=has_interrogate,
        has_semgrep=has_semgrep,
        has_make=has_make,
    )

    _cached_config = config
    return config
