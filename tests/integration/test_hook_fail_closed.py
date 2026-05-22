"""Subprocess coverage for the fail-closed hook entrypoint."""

import json
from pathlib import Path

import pytest

from tests.integration._helpers import run_hook
from tests.support._assertions import assert_blocked_shape


def test_hook_blocks_raw_pytest_pre_tool_use_event(tmp_path: Path) -> None:
    result = run_hook(_event("pytest tests/unit"), cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert_blocked_shape(result.stderr, "Use 'dev test'")


def test_hook_allows_dev_test_lane_command(tmp_path: Path) -> None:
    result = run_hook(_event("dev test --lane=unit"), cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_hook_malformed_json_fails_closed(tmp_path: Path) -> None:
    result = run_hook("{not valid json", cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert_blocked_shape(result.stderr, "Invalid PreToolUse input is not valid JSON")


@pytest.mark.parametrize(
    ("pyproject", "fragment"),
    [
        (
            '[tool.dev-cli.hook]\ndisable = ["no-raw-pytest"]\n',
            "pyproject.toml:2 because it is bedrock",
        ),
        (
            """[tool.dev-cli.hook]

[[tool.dev-cli.hook.rules]]
id = "project-no-deploy"
message = "BLOCKED: deploys are blocked. Use the release workflow instead."
""",
            "pyproject.toml:3 because match is missing",
        ),
    ],
)
def test_hook_invalid_config_fails_closed_with_file_line(
    pyproject: str,
    fragment: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    result = run_hook(_event("dev check"), cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert_blocked_shape(result.stderr, fragment)


def _event(command: str) -> str:
    return json.dumps({"tool_input": {"command": command}})
