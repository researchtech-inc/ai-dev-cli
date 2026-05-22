"""End-to-end probe command coverage."""

from pathlib import Path

from tests.integration._helpers import copy_integration_fixture, run_dev


def test_dev_probe_runs_configured_target_with_pytest_args(tmp_path: Path) -> None:
    project = copy_integration_fixture("probe-target", tmp_path)

    result = run_dev(("probe", "smoke", "--", "--echo=hello"), cwd=project, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert "Details: .tmp/dev-runs/" in result.stdout
    assert tuple((project / ".tmp" / "dev-runs").glob("*-probe-smoke.log"))
