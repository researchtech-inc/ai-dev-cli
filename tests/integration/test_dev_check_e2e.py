"""End-to-end dev check delegation tests."""

from pathlib import Path

from tests.integration._helpers import copy_integration_fixture, run_dev
from tests.support._assertions import assert_blocked_shape


def test_dev_check_blocks_raw_pre_commit_pytest_then_passes_after_delegation(tmp_path: Path) -> None:
    project = copy_integration_fixture("dev-check-raw-pytest", tmp_path)

    blocked = run_dev(("check",), cwd=project, timeout=180)

    assert blocked.returncode == 1
    assert blocked.stderr == ""
    assert "FAIL  bedrock-delegation" in blocked.stdout
    blocked_lines = [line.strip() for line in blocked.stdout.splitlines() if line.strip().startswith("BLOCKED:")]
    assert len(blocked_lines) == 1
    assert_blocked_shape(blocked_lines[0], ".pre-commit-config.yaml:")
    assert "invokes raw pytest in a quality surface" in blocked_lines[0]

    pre_commit = project / ".pre-commit-config.yaml"
    pre_commit.write_text(
        pre_commit.read_text(encoding="utf-8").replace("pytest tests/unit", "dev test --lane=unit"),
        encoding="utf-8",
    )

    delegated = run_dev(("check",), cwd=project, timeout=180)

    assert delegated.returncode == 0, delegated.stdout + delegated.stderr
    assert delegated.stderr == ""
    assert "FAIL  bedrock-delegation" not in delegated.stdout
    assert "invokes raw pytest" not in delegated.stdout
