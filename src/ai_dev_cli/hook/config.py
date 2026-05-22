"""Hook config loading and validation."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never

from ai_dev_cli._project import find_repo_root
from ai_dev_cli.hook.rules import (
    BEDROCK_RULE_IDS,
    BUILTIN_RULE_IDS,
    DISABLEABLE_RULE_IDS,
    HookRule,
    build_project_command_rule,
    build_project_env_rule,
)

CLAUDE_SETTINGS_JSON = """{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python -m ai_dev_cli.hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
"""

CODEX_CONFIG_TOML = """[features]
hooks = true

[hooks]
[[hooks.PreToolUse]]
matcher = "^Bash$"

[[hooks.PreToolUse.hooks]]
type = "command"
command = "python -m ai_dev_cli.hook"
timeout = 10
statusMessage = "checking dev CLI command policy"
"""

_LOWER_KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_BLOCK_MESSAGE_RE = re.compile(r"^BLOCKED: .+\. Use .+ instead\.$")


@dataclass(frozen=True, slots=True)
class HookConfig:
    """Validated hook configuration."""

    disabled_builtin_rule_ids: frozenset[str] = frozenset()
    project_rules: tuple[HookRule, ...] = ()
    enabled_builtin_rule_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class HookConfigError(Exception):
    """Fail-closed hook config error rendered by the hook entrypoint."""

    message: str


@dataclass(frozen=True, slots=True)
class _LineIndex:
    lines: tuple[str, ...]

    def hook_key(self, key: str) -> int:
        start = self._section_line("[tool.dev-cli.hook]")
        if start is None:
            return 1
        for index in range(start, len(self.lines)):
            text = self.lines[index].strip()
            if index > start and text.startswith("["):
                return start + 1
            if re.match(rf"{re.escape(key)}\s*=", text):
                return index + 1
        return start + 1

    def rule_table(self, index: int) -> int:
        seen = 0
        for line_number, line in enumerate(self.lines, 1):
            if line.strip() == "[[tool.dev-cli.hook.rules]]":
                if seen == index:
                    return line_number
                seen += 1
        return self.hook_key("rules")

    def rule_key(self, index: int, key: str) -> int:
        start = self.rule_table(index)
        for line_number in range(start, len(self.lines) + 1):
            text = self.lines[line_number - 1].strip()
            if line_number > start and text == "[[tool.dev-cli.hook.rules]]":
                return start
            if re.match(rf"{re.escape(key)}\s*=", text):
                return line_number
        return start

    def _section_line(self, section: str) -> int | None:
        for line_number, line in enumerate(self.lines):
            if line.strip() == section:
                return line_number
        return None


def load_hook_config(start: Path | None = None) -> HookConfig:
    """Load and validate [tool.dev-cli.hook] from pyproject.toml."""
    root = find_repo_root(start)
    pyproject_path = root / "pyproject.toml"
    if not pyproject_path.is_file():
        return HookConfig()

    text = pyproject_path.read_text(encoding="utf-8")
    line_index = _LineIndex(tuple(text.splitlines()))
    try:
        pyproject = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        line = getattr(exc, "lineno", 1)
        _raise_invalid(line, "pyproject.toml", "TOML could not be parsed", "a valid pyproject.toml")

    hook = pyproject.get("tool", {}).get("dev-cli", {}).get("hook")
    if hook is None:
        return HookConfig()
    if not isinstance(hook, dict):
        _raise_invalid(line_index.hook_key("hook"), "[tool.dev-cli.hook]", "it is not a table", "a hook table")

    disabled = _validate_disabled(hook.get("disable", ()), line_index)
    project_rules = _validate_project_rules(hook.get("rules", ()), line_index)
    return HookConfig(disabled_builtin_rule_ids=disabled, project_rules=project_rules)


def _validate_disabled(raw_disable: Any, line_index: _LineIndex) -> frozenset[str]:
    line = line_index.hook_key("disable")
    if raw_disable is None or raw_disable == ():
        return frozenset()
    if not isinstance(raw_disable, list):
        _raise_invalid(line, "[tool.dev-cli.hook].disable", "it is not a list", "a list of soft hook rule IDs")

    disabled: set[str] = set()
    for item in raw_disable:
        if not isinstance(item, str):
            _raise_invalid(line, "[tool.dev-cli.hook].disable", "it contains a non-string ID", "soft hook rule IDs")
        if item not in BUILTIN_RULE_IDS:
            _raise_invalid(
                line,
                f"[tool.dev-cli.hook].disable entry '{item}'",
                "it is unknown",
                "only soft hook rule IDs",
            )
        if item in BEDROCK_RULE_IDS:
            _raise_invalid(
                line,
                f"[tool.dev-cli.hook].disable entry '{item}'",
                "it is bedrock",
                "only soft hook rule IDs",
            )
        if item not in DISABLEABLE_RULE_IDS:
            _raise_invalid(
                line,
                f"[tool.dev-cli.hook].disable entry '{item}'",
                "it is not disableable",
                "only soft hook rule IDs",
            )
        disabled.add(item)
    return frozenset(disabled)


def _validate_project_rules(raw_rules: Any, line_index: _LineIndex) -> tuple[HookRule, ...]:
    if raw_rules is None or raw_rules == ():
        return ()
    if not isinstance(raw_rules, list):
        _raise_invalid(
            line_index.hook_key("rules"),
            "[tool.dev-cli.hook].rules",
            "it is not an array of tables",
            "valid [[tool.dev-cli.hook.rules]] entries",
        )

    seen: set[str] = set()
    rules: list[HookRule] = []
    for index, raw_rule in enumerate(raw_rules):
        line = line_index.rule_table(index)
        if not isinstance(raw_rule, dict):
            _raise_invalid(line, "[[tool.dev-cli.hook.rules]] entry", "it is not a table", "a valid rule table")

        rule_id = _validate_rule_id(raw_rule.get("id"), index, line_index, seen)
        message = _validate_rule_message(raw_rule.get("message"), rule_id, index, line_index)
        rules.append(_build_project_rule(rule_id, raw_rule.get("match"), message, index, line_index))
    return tuple(rules)


def _validate_rule_id(raw_id: Any, index: int, line_index: _LineIndex, seen: set[str]) -> str:
    line = line_index.rule_key(index, "id")
    if not isinstance(raw_id, str) or not _LOWER_KEBAB_RE.match(raw_id):
        _raise_invalid(
            line,
            "[[tool.dev-cli.hook.rules]].id",
            "it is not a lower-kebab stable ID",
            "a lower-kebab project rule ID",
        )
    if raw_id in BUILTIN_RULE_IDS:
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{raw_id}'",
            "it duplicates a built-in rule ID",
            "a unique project rule ID",
        )
    if raw_id in seen:
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{raw_id}'",
            "it duplicates a project rule ID",
            "a unique project rule ID",
        )
    seen.add(raw_id)
    return raw_id


def _validate_rule_message(raw_message: Any, rule_id: str, index: int, line_index: _LineIndex) -> str:
    line = line_index.rule_key(index, "message")
    if not isinstance(raw_message, str) or "\n" in raw_message or _BLOCK_MESSAGE_RE.match(raw_message) is None:
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}' message",
            "it does not use the required block-message shape",
            "BLOCKED: <why>. Use <replacement> instead.",
        )
    return raw_message


def _build_project_rule(
    rule_id: str,
    raw_match: Any,
    message: str,
    index: int,
    line_index: _LineIndex,
) -> HookRule:
    line = line_index.rule_key(index, "match")
    if not isinstance(raw_match, dict):
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}'",
            "match is missing",
            "one command match or one env match",
        )

    has_command = "command" in raw_match
    has_env = "env" in raw_match
    if has_command == has_env:
        reason = "match has both command and env" if has_command else "match is missing"
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}'",
            reason,
            "one command match or one env match",
        )
    if set(raw_match) - {"command", "env"}:
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}' match",
            "it has an unsupported key",
            "one command match or one env match",
        )
    if has_command:
        return _build_command_project_rule(rule_id, raw_match["command"], message, line)
    return _build_env_project_rule(rule_id, raw_match["env"], message, line)


def _build_command_project_rule(rule_id: str, raw_command: Any, message: str, line: int) -> HookRule:
    if not isinstance(raw_command, str):
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}' match.command",
            "it is not a regex string",
            "a command regex",
        )
    try:
        return build_project_command_rule(rule_id, raw_command, message)
    except re.error as exc:
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}' match.command",
            f"the regex is invalid: {exc}",
            "a valid command regex",
        )


def _build_env_project_rule(rule_id: str, raw_env: Any, message: str, line: int) -> HookRule:
    if (
        not isinstance(raw_env, dict)
        or not raw_env
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in raw_env.items())
    ):
        _raise_invalid(
            line,
            f"[[tool.dev-cli.hook.rules]] entry '{rule_id}' match.env",
            "it is not a string env table",
            "a non-empty env table",
        )
    return build_project_env_rule(rule_id, raw_env, message)


def _raise_invalid(line: int, subject: str, reason: str, replacement: str) -> Never:
    raise HookConfigError(
        f"BLOCKED: Invalid {subject} in pyproject.toml:{line} because {reason}. Use {replacement} instead."
    )
