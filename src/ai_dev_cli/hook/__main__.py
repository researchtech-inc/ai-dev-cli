"""Run the ai_dev_cli PreToolUse hook."""

import sys

from ai_dev_cli.hook.config import HookConfigError, load_hook_config
from ai_dev_cli.hook.engine import check_command
from ai_dev_cli.hook.events import HookInputError, parse_pre_tool_use


def main() -> int:
    """Read one PreToolUse event from stdin and evaluate hook policy."""
    try:
        command = parse_pre_tool_use(sys.stdin.read())
        decision = check_command(command, load_hook_config())
    except (HookInputError, HookConfigError) as exc:
        sys.stderr.write(f"{exc.message}\n")
        return 2

    if not decision.allowed:
        sys.stderr.write(f"{decision.message}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
