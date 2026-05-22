"""Hook engine and rule matcher tests."""

import pytest

from ai_dev_cli.hook.config import HookConfig
from ai_dev_cli.hook.engine import check_command
from ai_dev_cli.hook.rules import BEDROCK_RULE_IDS, BUILTIN_RULES, build_project_command_rule

RULES = {rule.id: rule for rule in BUILTIN_RULES}


@pytest.mark.parametrize(
    ("rule_id", "blocked", "allowed"),
    [
        ("no-dev-testmon-cache-flags", "dev test --force", "dev test --rerun-failed"),
        ("no-dev-removed-lane-flags", "dev test --integration", "dev test --lane=integration"),
        ("no-dev-lf", "dev test --lf", "dev test --rerun-failed"),
        ("no-dev-removed-commands", "dev ci", "dev check"),
        ("no-dev-execution-control-flags", "dev test --timeout 5", "dev test --lane=unit"),
        ("no-env-or-command-wrapped-live", "FOO=bar dev verify", "FOO=bar dev check"),
        ("no-dev-verify", "dev verify", "dev check"),
        ("no-dev-benchmark", "dev benchmark", "dev check"),
        ("no-live-examples", "dev examples --live", "dev examples --smoke"),
        ("no-python-dev-live-entrypoint", "python -m ai_dev_cli verify", "python -m ai_dev_cli info"),
        ("no-run-benchmark-env", "RUN_BENCHMARK=1 pytest tests/unit", "RUN_BENCHMARK=1 dev benchmark"),
        ("no-piped-output", "dev test 2>&1 | head", "dev test"),
        ("no-raw-pytest", "python -m pytest tests/unit", "pytest --help"),
        ("no-raw-ruff", "ruff check .", "ruff check --help"),
        ("no-raw-typecheck", "python -m mypy .", "mypy --help"),
        ("no-uv-sync", "uv sync", "uv pip list"),
        ("no-uv-venv", "python -m venv .venv", "uv pip install -e ."),
        ("no-dunder-import", "python -c \"__import__('ai_dev_cli')\"", "python -c 'import ai_dev_cli'"),
    ],
)
def test_each_bedrock_rule_matches_and_allows_expected_examples(
    rule_id: str,
    blocked: str,
    allowed: str,
) -> None:
    assert rule_id in BEDROCK_RULE_IDS
    assert RULES[rule_id].matches(blocked) is True
    assert RULES[rule_id].matches(allowed) is False


def test_soft_disable_allowlist_gating_disables_uv_run() -> None:
    blocked = check_command("uv run dev test")
    allowed = check_command("uv run dev test", HookConfig(disabled_builtin_rule_ids=frozenset({"no-uv-run"})))

    assert blocked.allowed is False
    assert blocked.rule_id == "no-uv-run"
    assert allowed.allowed is True


def test_default_disabled_lane_rules_block_only_when_enabled() -> None:
    default = check_command("dev test --lane=integration")
    enabled = check_command(
        "dev test --lane=integration",
        HookConfig(enabled_builtin_rule_ids=frozenset({"no-integration-lane"})),
    )

    assert default.allowed is True
    assert enabled.allowed is False
    assert enabled.rule_id == "no-integration-lane"


def test_project_rules_run_after_bedrock_rules() -> None:
    project_rule = build_project_command_rule(
        "project-no-deploy",
        "deploy-production",
        "BLOCKED: deploys are blocked. Use the release workflow instead.",
    )
    config = HookConfig(project_rules=(project_rule,))

    assert check_command("deploy-production", config).rule_id == "project-no-deploy"
    assert check_command("pytest tests/unit", config).rule_id == "no-raw-pytest"
