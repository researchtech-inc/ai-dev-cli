"""Built-in and project hook rules."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

BEDROCK = "bedrock"
SOFT = "soft"

# Matches command position: start of string, after shell operators, after (, or inside shell wrapper quotes.
_CMD = r"(?:^|[;&|('\"]\s*)"
_LIVE_DEV_COMMAND = r"dev\s+(?:verify|benchmark)\b"
_LIVE_DEV_EXAMPLES = r"dev\s+examples\b[^\n;|&]*?--live\b"
_PYTHON_DEV = r"python3?\s+-m\s+ai_dev_cli(?:\.main)?"
_LIVE_PYTHON_DEV_COMMAND = _PYTHON_DEV + r"\s+(?:verify|benchmark)\b"
_LIVE_PYTHON_DEV_EXAMPLES = _PYTHON_DEV + r"\s+examples\b[^\n;|&]*?--live\b"
_LIVE_DIRECT_PARTS = (_LIVE_DEV_COMMAND, _LIVE_DEV_EXAMPLES, _LIVE_PYTHON_DEV_COMMAND, _LIVE_PYTHON_DEV_EXAMPLES)
_LIVE_DIRECT_COMMAND = r"(?:" + "|".join(_LIVE_DIRECT_PARTS) + r")"
_ENV_OR_COMMAND = r"(?:(?:\w+=\S+\s+)+|env(?:\s+(?:-\S+|\w+=\S+))*\s+|command\s+)"
_PIPE_SINK = r"(?:grep|head|tail|less|more)"
_HELP_OR_VERSION = re.compile(r"(?:^|\s)(?:--version|--help|-h)(?:\s|$)")
_ASSIGNMENT_RE = re.compile(r"(?P<key>[A-Za-z_]\w*)=(?P<value>[^\s;&|]+)")
_LEADING_ASSIGNMENTS_RE = re.compile(_CMD + r"(?P<assignments>(?:[A-Za-z_]\w*=[^\s;&|]+\s+)+)(?P<next>\S+)")
_ENV_COMMAND_RE = re.compile(_CMD + r"env(?P<args>(?:\s+(?:-\S+|[A-Za-z_]\w*=[^\s;&|]+))*)\s+(?P<next>\S[^\n;|&]*)")


@dataclass(frozen=True, slots=True)
class HookRule:
    """One executable hook rule."""

    id: str
    status: str
    default_enabled: bool
    message: str
    matches: Callable[[str], bool]


def _regex(pattern: str, flags: int = 0) -> Callable[[str], bool]:
    compiled = re.compile(pattern, flags)
    return lambda command: compiled.search(command) is not None


def _has_help_or_version(args: str) -> bool:
    return _HELP_OR_VERSION.search(args) is not None


def _raw_pytest(command: str) -> bool:
    pattern = re.compile(_CMD + r"(?P<tool>(?:python3?\s+-m\s+)?pytest)(?=\s|$)(?P<args>[^\n;|&)]*)")
    for match in pattern.finditer(command):
        if _has_help_or_version(match.group("args")):
            continue
        return True
    return False


def _raw_ruff(command: str) -> bool:
    pattern = re.compile(_CMD + r"(?P<tool>(?:python3?\s+-m\s+)?ruff)(?=\s|$)(?P<args>[^\n;|&)]*)")
    for match in pattern.finditer(command):
        args = match.group("args")
        if _has_help_or_version(args):
            continue
        if re.search(r"(?:^|\s)(?:check|format)(?:\s|$)", args):
            return True
    return False


def _raw_typechecker(command: str) -> bool:
    tools = r"(?:basedpyright|pyright|mypy)"
    pattern = re.compile(_CMD + rf"(?P<tool>(?:python3?\s+-m\s+)?{tools})(?=\s|$)(?P<args>[^\n;|&)]*)")
    for match in pattern.finditer(command):
        if _has_help_or_version(match.group("args")):
            continue
        return True
    return False


def _piped_output(command: str) -> bool:
    dev_pipe = re.compile(rf"\bdev\s+\S+[^\n|]*(?:\s+2>&1\s*)?\|\s*{_PIPE_SINK}\b")
    pytest_pipe = re.compile(rf"(?:python3?\s+-m\s+)?pytest(?=\s|$)[^\n|]*(?:\s+2>&1\s*)?\|\s*{_PIPE_SINK}\b")
    return dev_pipe.search(command) is not None or pytest_pipe.search(command) is not None


def _assignment_map(text: str) -> dict[str, str]:
    return {match.group("key"): match.group("value") for match in _ASSIGNMENT_RE.finditer(text)}


def _has_expected_env(assignments: Mapping[str, str], expected: Mapping[str, str]) -> bool:
    return all(assignments.get(key) == value for key, value in expected.items())


def command_has_env(command: str, expected: Mapping[str, str]) -> bool:
    """Match leading KEY=value assignments or env KEY=value command forms."""
    for match in _LEADING_ASSIGNMENTS_RE.finditer(command):
        if _has_expected_env(_assignment_map(match.group("assignments")), expected):
            return True
    for match in _ENV_COMMAND_RE.finditer(command):
        if _has_expected_env(_assignment_map(match.group("args")), expected):
            return True
    return False


def _run_benchmark_env(command: str) -> bool:
    expected = {"RUN_BENCHMARK": "1"}
    for match in _LEADING_ASSIGNMENTS_RE.finditer(command):
        if _has_expected_env(_assignment_map(match.group("assignments")), expected):
            remainder = command[match.start("next") :]
            if not re.match(r"dev\s+benchmark\b", remainder):
                return True
    for match in _ENV_COMMAND_RE.finditer(command):
        if _has_expected_env(_assignment_map(match.group("args")), expected):
            next_command = match.group("next").lstrip()
            if not re.match(r"dev\s+benchmark\b", next_command):
                return True
    return False


def build_project_command_rule(rule_id: str, pattern: str, message: str) -> HookRule:
    """Build a project-owned command regex rule after config validation."""
    compiled = re.compile(pattern)
    return HookRule(
        id=rule_id,
        status=SOFT,
        default_enabled=True,
        message=message,
        matches=lambda command: compiled.search(command) is not None,
    )


def build_project_env_rule(rule_id: str, expected: Mapping[str, str], message: str) -> HookRule:
    """Build a project-owned env rule after config validation."""
    expected_values = dict(expected)
    return HookRule(
        id=rule_id,
        status=SOFT,
        default_enabled=True,
        message=message,
        matches=lambda command: command_has_env(command, expected_values),
    )


BUILTIN_RULES: tuple[HookRule, ...] = (
    HookRule(
        id="no-dev-testmon-cache-flags",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: --force and --no-testmon bypass dev's testmon and cache-safety model. "
            "Use 'dev test' or 'dev test --rerun-failed' instead."
        ),
        matches=_regex(_CMD + r"dev\s+test\b[^\n;|&]*?--(?:force|no-testmon)\b"),
    ),
    HookRule(
        id="no-dev-removed-lane-flags",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Removed dev test lane flags bypass the current lane model and can trigger live tests. "
            "Use 'dev test --lane=unit' or 'dev check' instead."
        ),
        matches=_regex(_CMD + r"dev\s+test\b[^\n;|&]*?--(?:full|available|integration|all)\b"),
    ),
    HookRule(
        id="no-dev-lf",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: --lf is removed and does not express the scoped rerun contract. "
            "Use 'dev test --rerun-failed' instead."
        ),
        matches=_regex(_CMD + r"dev\s+test\b[^\n;|&]*?--lf\b"),
    ),
    HookRule(
        id="no-dev-removed-commands",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Removed dev commands bypass the current workflow surface. "
            "Use 'dev check' for validation or 'dev info' for guidance instead."
        ),
        matches=_regex(_CMD + r"dev\s+(?:ci|list-tests)\b"),
    ),
    HookRule(
        id="no-dev-execution-control-flags",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Execution-control flags bypass dev's lane-owned coverage, worker, and timeout settings. "
            "Use 'dev test --lane=unit' or 'dev check' instead."
        ),
        matches=_regex(
            _CMD + r"dev\s+test\b[^\n;|&]*?(?:--coverage\b|(?:^|\s)(?:-n|--num-workers|--timeout)(?:[=\s]|$))"
        ),
    ),
    HookRule(
        id="no-env-or-command-wrapped-live",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Wrapped dev commands can hide live or broad validation from the agent policy. "
            "Use 'dev check' instead."
        ),
        matches=_regex(_CMD + _ENV_OR_COMMAND + _LIVE_DIRECT_COMMAND),
    ),
    HookRule(
        id="no-dev-verify",
        status=BEDROCK,
        default_enabled=True,
        message="BLOCKED: dev verify runs broad validation outside the agent budget. Use 'dev check' instead.",
        matches=_regex(_CMD + r"dev\s+verify\b"),
    ),
    HookRule(
        id="no-dev-benchmark",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: dev benchmark can run live benchmark validation outside the agent budget. "
            "Use 'dev check' instead."
        ),
        matches=_regex(_CMD + r"dev\s+benchmark\b"),
    ),
    HookRule(
        id="no-live-examples",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Live examples make external LLM calls. "
            "Use 'dev examples --static' or 'dev examples --smoke' instead."
        ),
        matches=_regex(_CMD + r"dev\s+examples\b[^\n;|&]*?--live\b"),
    ),
    HookRule(
        id="no-python-dev-live-entrypoint",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Python-module dev entrypoints can bypass live and broad validation guardrails. "
            "Use 'dev check' instead."
        ),
        matches=_regex(_CMD + r"(?:" + _LIVE_PYTHON_DEV_COMMAND + r"|" + _LIVE_PYTHON_DEV_EXAMPLES + r")"),
    ),
    HookRule(
        id="no-run-benchmark-env",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: RUN_BENCHMARK=1 can force live benchmark collection outside dev benchmark. "
            "Use 'dev check' in this session instead."
        ),
        matches=_run_benchmark_env,
    ),
    HookRule(
        id="no-piped-output",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Piping dev or pytest output can hide failures and hang through buffering. "
            "Use the command directly and read the captured .tmp/dev-runs log instead."
        ),
        matches=_piped_output,
    ),
    HookRule(
        id="no-raw-pytest",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Raw pytest bypasses dev's lane, cache, timeout, and log controls. "
            "Use 'dev test', 'dev test --lane=unit', or 'dev test --rerun-failed' instead."
        ),
        matches=_raw_pytest,
    ),
    HookRule(
        id="no-raw-ruff",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Raw ruff bypasses dev's ordered lint and format workflow and logging. "
            "Use 'dev lint', 'dev format', or 'dev check' instead."
        ),
        matches=_raw_ruff,
    ),
    HookRule(
        id="no-raw-typecheck",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Raw typecheckers bypass dev's configured project and test typecheck flow. "
            "Use 'dev typecheck' or 'dev check' instead."
        ),
        matches=_raw_typechecker,
    ),
    HookRule(
        id="no-uv-sync",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: uv sync creates a local .venv in the prepared devcontainer environment. "
            "Use the installed dev commands already on PATH instead."
        ),
        matches=_regex(_CMD + r"uv\s+sync\b"),
    ),
    HookRule(
        id="no-uv-run",
        status=SOFT,
        default_enabled=True,
        message=(
            "BLOCKED: uv run is an unnecessary wrapper around tools already on PATH in the prepared devcontainer "
            "environment. Use the underlying command, for example 'dev test', instead."
        ),
        matches=_regex(_CMD + r"uv\s+run\s"),
    ),
    HookRule(
        id="no-uv-venv",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: Creating a local virtualenv conflicts with the prepared devcontainer install. "
            "Use the installed tools already on PATH instead."
        ),
        matches=_regex(_CMD + r"(?:uv\s+venv|python3?\s+-m\s+venv|virtualenv)\b"),
    ),
    HookRule(
        id="no-dunder-import",
        status=BEDROCK,
        default_enabled=True,
        message=(
            "BLOCKED: __import__() command snippets can hide protected tool invocations. "
            "Use the named dev command or importlib.import_module() in source code instead."
        ),
        matches=_regex(r"__import__\s*\("),
    ),
    HookRule(
        id="no-integration-lane",
        status=SOFT,
        default_enabled=False,
        message=(
            "BLOCKED: The project does not allow integration lane tests in agent sessions. "
            "Use 'dev test --lane=unit' or 'dev check' instead."
        ),
        matches=_regex(_CMD + r"dev\s+test\b[^\n;|&]*?--lane(?:=|\s+)integration\b"),
    ),
    HookRule(
        id="no-qualification-lane",
        status=SOFT,
        default_enabled=False,
        message=(
            "BLOCKED: The project does not allow qualification lane tests in agent sessions. "
            "Use 'dev test --lane=unit' or 'dev check' instead."
        ),
        matches=_regex(_CMD + r"dev\s+test\b[^\n;|&]*?--lane(?:=|\s+)qualification\b"),
    ),
)

BUILTIN_RULE_IDS = frozenset(rule.id for rule in BUILTIN_RULES)
BEDROCK_RULE_IDS = frozenset(rule.id for rule in BUILTIN_RULES if rule.status == BEDROCK)
SOFT_RULE_IDS = frozenset(rule.id for rule in BUILTIN_RULES if rule.status == SOFT)
DISABLEABLE_RULE_IDS = frozenset({"no-uv-run", "no-integration-lane", "no-qualification-lane"})


def enabled_builtin_rules(
    disabled_rule_ids: frozenset[str],
    explicitly_enabled_rule_ids: frozenset[str] = frozenset(),
) -> tuple[HookRule, ...]:
    """Return built-ins enabled for one hook execution."""
    return tuple(
        rule
        for rule in BUILTIN_RULES
        if rule.id not in disabled_rule_ids and (rule.default_enabled or rule.id in explicitly_enabled_rule_ids)
    )
