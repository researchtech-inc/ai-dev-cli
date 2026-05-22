"""Hook config loading and validation tests."""

from pathlib import Path

import pytest

from ai_dev_cli.hook.config import HookConfigError, load_hook_config
from tests.support._assertions import assert_blocked_shape
from tests.unit._helpers import write_pyproject


def test_load_hook_config_accepts_soft_disable_and_project_rule(tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """[tool.dev-cli.hook]
disable = ["no-uv-run", "no-integration-lane"]

[[tool.dev-cli.hook.rules]]
id = "project-no-deploy"
message = "BLOCKED: deploys are blocked. Use the release workflow instead."

[tool.dev-cli.hook.rules.match]
command = "\\bdeploy-production\\b"
""",
    )

    config = load_hook_config(tmp_path)

    assert config.disabled_builtin_rule_ids == frozenset({"no-uv-run", "no-integration-lane"})
    assert len(config.project_rules) == 1
    assert config.project_rules[0].id == "project-no-deploy"


def test_load_hook_config_walks_up_from_nested_path(tmp_path: Path) -> None:
    write_pyproject(tmp_path, '[tool.dev-cli.hook]\ndisable = ["no-uv-run"]\n')
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)

    config = load_hook_config(nested)

    assert config.disabled_builtin_rule_ids == frozenset({"no-uv-run"})


def test_hook_config_rejects_bedrock_rule_in_disable_with_line(tmp_path: Path) -> None:
    write_pyproject(tmp_path, '[tool.dev-cli.hook]\ndisable = ["no-raw-pytest"]\n')

    with pytest.raises(HookConfigError) as exc_info:
        load_hook_config(tmp_path)

    message = exc_info.value.message
    assert_blocked_shape(message)
    assert "pyproject.toml:2" in message
    assert "because it is bedrock" in message


def test_hook_config_rejects_unknown_disable_entry(tmp_path: Path) -> None:
    write_pyproject(tmp_path, '[tool.dev-cli.hook]\ndisable = ["project-nope"]\n')

    with pytest.raises(HookConfigError) as exc_info:
        load_hook_config(tmp_path)

    message = exc_info.value.message
    assert_blocked_shape(message)
    assert "pyproject.toml:2" in message
    assert "because it is unknown" in message


def test_hook_config_rejects_non_list_disable(tmp_path: Path) -> None:
    write_pyproject(tmp_path, '[tool.dev-cli.hook]\ndisable = "no-uv-run"\n')

    with pytest.raises(HookConfigError) as exc_info:
        load_hook_config(tmp_path)

    assert_blocked_shape(exc_info.value.message, "Invalid [tool.dev-cli.hook].disable in pyproject.toml:2")


def test_hook_config_rejects_project_rule_missing_match_with_line(tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """[tool.dev-cli.hook]

[[tool.dev-cli.hook.rules]]
id = "project-no-deploy"
message = "BLOCKED: deploys are blocked. Use the release workflow instead."
""",
    )

    with pytest.raises(HookConfigError) as exc_info:
        load_hook_config(tmp_path)

    assert_blocked_shape(
        exc_info.value.message,
        "entry 'project-no-deploy' in pyproject.toml:3 because match is missing",
    )


def test_hook_config_rejects_project_rule_with_both_command_and_env(tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """[tool.dev-cli.hook]

[[tool.dev-cli.hook.rules]]
id = "project-no-mixed-match"
message = "BLOCKED: mixed match is invalid. Use one match shape instead."

[tool.dev-cli.hook.rules.match]
command = "deploy"

[tool.dev-cli.hook.rules.match.env]
LIVE_PROFILE = "1"
""",
    )

    with pytest.raises(HookConfigError) as exc_info:
        load_hook_config(tmp_path)

    message = exc_info.value.message
    assert_blocked_shape(message)
    assert "because match has both command and env" in message
    assert "pyproject.toml:3" in message


def test_hook_config_rejects_bad_project_rule_message_shape(tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """[tool.dev-cli.hook]

[[tool.dev-cli.hook.rules]]
id = "project-no-deploy"
message = "deploys are blocked"

[tool.dev-cli.hook.rules.match]
command = "deploy"
""",
    )

    with pytest.raises(HookConfigError) as exc_info:
        load_hook_config(tmp_path)

    message = exc_info.value.message
    assert_blocked_shape(message)
    assert "message in pyproject.toml:5" in message
    assert "required block-message shape" in message
