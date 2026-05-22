"""End-to-end pytest plugin subprocess tests against R29 fixtures."""

import subprocess
from pathlib import Path

import pytest

from tests.integration._helpers import ARTIFACT_ROOT, copy_project_fixture, run_dev, run_module
from tests.support._assertions import assert_blocked_shape

PYTEST_PLUGIN_FIXTURES = ARTIFACT_ROOT / "tests" / "fixtures" / "pytest_plugin"


@pytest.mark.parametrize(
    "fixture_name",
    ["lane-derives-from-path", "per-test-override-wins", "opt-out-disables-plugin"],
)
def test_pytest_plugin_fixture_project_passes(fixture_name: str, tmp_path: Path) -> None:
    project = copy_project_fixture(PYTEST_PLUGIN_FIXTURES / fixture_name, tmp_path)

    result = _run_pytest(project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


def test_pytest_plugin_fails_closed_when_pytest_timeout_is_missing(tmp_path: Path) -> None:
    project = copy_project_fixture(PYTEST_PLUGIN_FIXTURES / "pytest-timeout-missing", tmp_path)
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(
        """import importlib.util

_original_find_spec = importlib.util.find_spec


def _find_spec(name, package=None):
    if name == "pytest_timeout":
        return None
    return _original_find_spec(name, package)


importlib.util.find_spec = _find_spec
""",
        encoding="utf-8",
    )

    result = _run_pytest(project, extra_pythonpath=(blocker,))

    assert result.returncode == 2
    assert_blocked_shape(result.stderr.splitlines()[0], "requires pytest_timeout")


def test_dev_test_runs_pytest_plugin_through_runner_path(tmp_path: Path) -> None:
    project = copy_project_fixture(PYTEST_PLUGIN_FIXTURES / "lane-derives-from-path", tmp_path)

    result = run_dev(
        ("test", "--lane=unit"),
        cwd=project,
        extra_env={"PYTEST_ADDOPTS": "-p no:ai_dev_cli -p ai_dev_cli._pytest_plugin"},
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert "PASS  test" in result.stdout


def _run_pytest(
    project: Path,
    *,
    extra_pythonpath: tuple[Path, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return run_module(
        "pytest",
        ("-p", "ai_dev_cli._pytest_plugin", "-q", "tests/unit"),
        cwd=project,
        extra_env={"PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        extra_pythonpath=extra_pythonpath,
        timeout=60,
    )
