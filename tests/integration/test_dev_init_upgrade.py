"""End-to-end dev init upgrade block and force-accept tests."""

from pathlib import Path

from tests.integration._helpers import (
    assert_sidecar_tracks,
    file_sha256,
    remove_sidecar_path,
    run_dev,
    sidecar_hash,
)
from tests.support._assertions import assert_blocked_shape

UNMANAGED_PATH = ".pre-commit-config.yaml"
DIVERGENT_PATH = "pyproject.toml"


def test_dev_init_upgrade_blocks_until_each_target_is_accepted(tmp_path: Path) -> None:
    project = tmp_path / "upgrade-project"
    project.mkdir()

    init = run_dev(("init", "--profile=minimal"), cwd=project)
    assert init.returncode == 0, init.stdout + init.stderr

    remove_sidecar_path(project, UNMANAGED_PATH)
    (project / UNMANAGED_PATH).write_text("# handcrafted pre-commit config\nrepos: []\n", encoding="utf-8")
    (project / DIVERGENT_PATH).write_text(
        (project / DIVERGENT_PATH).read_text(encoding="utf-8") + "\n# local edit\n",
        encoding="utf-8",
    )
    assert sidecar_hash(project, DIVERGENT_PATH) != file_sha256(project / DIVERGENT_PATH)

    plan = run_dev(("init", "--profile=minimal", "--upgrade", "--plan"), cwd=project)

    assert plan.returncode == 2
    _assert_upgrade_blocks(plan.stderr)

    apply = run_dev(("init", "--profile=minimal", "--upgrade", "--apply"), cwd=project)

    assert apply.returncode == 2
    _assert_upgrade_blocks(apply.stderr)

    forced = run_dev(
        (
            "init",
            "--profile=minimal",
            "--upgrade",
            "--apply",
            "--force",
            "--accept",
            UNMANAGED_PATH,
            "--accept",
            DIVERGENT_PATH,
        ),
        cwd=project,
    )

    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert forced.stderr == ""
    assert "local edit" not in (project / DIVERGENT_PATH).read_text(encoding="utf-8")
    assert "handcrafted" not in (project / UNMANAGED_PATH).read_text(encoding="utf-8")
    assert_sidecar_tracks(project, "minimal", (UNMANAGED_PATH, DIVERGENT_PATH))


def _assert_upgrade_blocks(stderr: str) -> None:
    lines = tuple(line for line in stderr.splitlines() if line.strip())
    assert len(lines) == 2
    for line in lines:
        assert_blocked_shape(line)
    assert any(f"{UNMANAGED_PATH} is unmanaged and would be overwritten" in line for line in lines)
    assert any(f"{DIVERGENT_PATH} was edited outside ai-dev-cli management" in line for line in lines)
