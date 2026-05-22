"""End-to-end dev init scaffold tests."""

from pathlib import Path

import pytest

from tests.integration._helpers import (
    FIXTURES_ROOT,
    assert_sidecar_tracks,
    assert_tree_matches_golden,
    copy_integration_fixture,
    run_dev,
)
from tests.support._assertions import assert_blocked_shape

PROFILES = (
    ("minimal", "minimal-fresh"),
    ("strict-python", "strict-python-fresh"),
    ("agent", "agent-fresh"),
)


@pytest.mark.parametrize(("profile", "fixture_name"), PROFILES)
def test_dev_init_profile_matches_golden_tree(profile: str, fixture_name: str, tmp_path: Path) -> None:
    project = copy_integration_fixture(fixture_name, tmp_path)

    result = run_dev(("init", f"--profile={profile}"), cwd=project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert (project / ".ai-dev-cli-managed").is_file()

    golden = FIXTURES_ROOT / "golden" / fixture_name
    assert_tree_matches_golden(project, golden)
    assert_sidecar_tracks(project, profile, _golden_files(golden))

    blocked = run_dev(("init", f"--profile={profile}"), cwd=project)

    assert blocked.returncode == 2
    assert blocked.stdout == ""
    assert_blocked_shape(blocked.stderr, "Use 'dev init --upgrade' instead.")


def _golden_files(golden: Path) -> tuple[str, ...]:
    return tuple(sorted(path.relative_to(golden).as_posix() for path in golden.rglob("*") if path.is_file()))
