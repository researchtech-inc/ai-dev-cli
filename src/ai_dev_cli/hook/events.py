"""PreToolUse event parsing for the hook entrypoint."""

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HookInputError(Exception):
    """Fail-closed input error rendered by the hook entrypoint."""

    message: str


def parse_pre_tool_use(raw_event: str) -> str:
    """Extract the Bash command from one PreToolUse JSON event."""
    try:
        event = json.loads(raw_event)
    except json.JSONDecodeError as exc:
        raise HookInputError(
            "BLOCKED: Invalid PreToolUse input is not valid JSON. "
            "Use a PreToolUse event with tool_input.command instead."
        ) from exc

    if not isinstance(event, dict):
        raise HookInputError(
            "BLOCKED: Invalid PreToolUse input is not a JSON object. "
            "Use a PreToolUse event with tool_input.command instead."
        )

    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        raise HookInputError(
            "BLOCKED: Invalid PreToolUse input is missing tool_input. "
            "Use a PreToolUse event with tool_input.command instead."
        )

    command: Any = tool_input.get("command")
    if not isinstance(command, str) or not command:
        raise HookInputError(
            "BLOCKED: Invalid PreToolUse input is missing tool_input.command. "
            "Use a PreToolUse event with tool_input.command instead."
        )

    return command
