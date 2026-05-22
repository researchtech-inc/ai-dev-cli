"""Hook command evaluation."""

from dataclasses import dataclass

from ai_dev_cli.hook.config import HookConfig
from ai_dev_cli.hook.rules import enabled_builtin_rules


@dataclass(frozen=True, slots=True)
class HookDecision:
    """Result of evaluating one shell command against hook policy."""

    allowed: bool
    rule_id: str | None = None
    message: str = ""


def check_command(command: str, config: HookConfig | None = None) -> HookDecision:
    """Return the first blocking rule decision for a command."""
    hook_config = config or HookConfig()
    rules = (
        *enabled_builtin_rules(hook_config.disabled_builtin_rule_ids, hook_config.enabled_builtin_rule_ids),
        *hook_config.project_rules,
    )
    for rule in rules:
        if rule.matches(command):
            return HookDecision(allowed=False, rule_id=rule.id, message=rule.message)
    return HookDecision(allowed=True)
