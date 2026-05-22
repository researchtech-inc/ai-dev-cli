"""Bootstrap module tests."""

from io import StringIO
from pathlib import Path
from shutil import copytree

import pytest

from ai_dev_cli import __version__
from ai_dev_cli.bootstrap import apply, disposition, main, profiles, sidecar
from tests.support._assertions import assert_blocked_shape
from tests.unit._helpers import write_custom_profile

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bootstrap"
PLAN_PATH = ".tmp/dev-runs/dev-init-upgrade-plan.csv"


def test_sidecar_round_trip(tmp_path: Path) -> None:
    data = sidecar.build_sidecar(
        profile="minimal",
        ai_dev_cli_version=__version__,
        written_at="2026-05-22T00:00:00Z",
        files=(
            sidecar.ManagedFile(
                path="pyproject.toml",
                template_id="minimal/pyproject.snippet.toml",
                template_version=1,
                sha256="abc123",
            ),
        ),
    )

    sidecar.write_sidecar(tmp_path, data)

    assert sidecar.read_sidecar(tmp_path) == data


def test_disposition_csv_round_trip(tmp_path: Path) -> None:
    rows = (
        disposition.DispositionRow(
            path="tests/test_smoke.py",
            target="examples/tests/test_smoke.py",
            disp="cheap",
            action="move",
            why="profile-layout correction",
            next="move",
        ),
        disposition.DispositionRow(
            path="tests/unit/test_options.py",
            target="tests/unit/test_options.py",
            disp="medium",
            action="keep",
            why="isolated fixture",
            next="manual review",
        ),
    )
    path = tmp_path / "plan.csv"

    disposition.write_csv(path, rows)

    assert disposition.parse_csv(path) == rows


def test_fresh_init_writes_minimal_profile_templates(tmp_path: Path) -> None:
    project = _copy_fixture("fresh-init", tmp_path)
    (project / ".keep").unlink()
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(["--profile=minimal"], root=project, stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert (project / ".ai-dev-cli-managed").is_file()
    profile = profiles.load_shipped_profile("minimal")
    for template in profile.templates:
        expected = profiles.render_template(profile, template, project_name=project.name)
        assert (project / template.target).read_bytes() == expected

    managed = sidecar.read_sidecar(project)
    assert managed is not None
    assert tuple(item.path for item in managed.files) == tuple(sorted(profiles.expected_targets(profile)))
    assert {item.template_id for item in managed.files} == {
        f"{profile.name}/{template.source}" for template in profile.templates
    }
    assert {item.template_version for item in managed.files} == {profile.version}
    assert main.AGENT_ONBOARDING in stdout.getvalue()


def test_first_init_blocks_preexisting_unmanaged_target_without_force(tmp_path: Path) -> None:
    project = _copy_fixture("fresh-init", tmp_path)
    (project / ".keep").unlink()
    existing = "# existing project config\n"
    (project / "pyproject.toml").write_text(existing, encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(["--profile=minimal"], root=project, stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert_blocked_shape(stderr.getvalue(), "pyproject.toml is unmanaged and would be overwritten")
    assert (project / "pyproject.toml").read_text(encoding="utf-8") == existing
    assert not (project / ".ai-dev-cli-managed").exists()

    force_out, force_err = StringIO(), StringIO()
    force_code = main.run(
        ["--profile=minimal", "--force", "--accept", "pyproject.toml"],
        root=project,
        stdout=force_out,
        stderr=force_err,
    )

    assert force_code == 0
    assert force_err.getvalue() == ""
    assert (project / "pyproject.toml").read_text(encoding="utf-8") != existing
    assert (project / ".ai-dev-cli-managed").is_file()


def test_profile_file_scaffold_smoke(tmp_path: Path) -> None:
    profile_root = tmp_path / "profile"
    project = tmp_path / "project"
    profile_root.mkdir()
    project.mkdir()
    write_custom_profile(profile_root)
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(["--profile-file", str(profile_root)], root=project, stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert (project / "generated.txt").read_text(encoding="utf-8") == "project = project\n"
    assert sidecar.read_sidecar(project) is not None


@pytest.mark.parametrize("name", ["strict-python", "agent"])
def test_shipped_profile_loads_strict_python_and_agent(name: str) -> None:
    profile = profiles.load_shipped_profile(name)

    assert profile.name == name
    assert ".codex/config.toml" in profiles.expected_targets(profile)
    for template in profile.templates:
        assert profile.root.joinpath(template.source).is_file()


def test_upgrade_modes_are_mutually_exclusive(tmp_path: Path) -> None:
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(
        ["--profile=minimal", "--upgrade", "--plan", "--apply"],
        root=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert_blocked_shape(stderr.getvalue(), "multiple upgrade modes were requested")


def test_first_init_blocks_when_sidecar_exists(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sidecar.write_sidecar(
        project,
        sidecar.build_sidecar(profile="minimal", ai_dev_cli_version=__version__, files=()),
    )
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(["--profile=minimal"], root=project, stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert_blocked_shape(stderr.getvalue(), "project already managed")


def test_upgrade_plan_blocks_divergent_file_and_matches_golden(tmp_path: Path) -> None:
    project = _copy_fixture("upgrade-plan", tmp_path)
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(["--profile=minimal", "--upgrade", "--plan"], root=project, stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert_blocked_shape(stderr.getvalue(), "pyproject.toml was edited outside ai-dev-cli management")
    assert f"Plan: {PLAN_PATH}\n" in stdout.getvalue()
    assert _plan_csv(project, stdout.getvalue()).read_bytes() == (FIXTURES / "upgrade-plan" / "golden.csv").read_bytes()


def test_upgrade_apply_with_force_accept_updates_sidecar_and_blocks_absent_recover(tmp_path: Path) -> None:
    project = _copy_fixture("upgrade-apply-with-force-accept", tmp_path)
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(
        ["--profile=minimal", "--upgrade", "--apply", "--force", "--accept", "pyproject.toml"],
        root=project,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert f"Plan: {PLAN_PATH}\n" in stdout.getvalue()
    assert (
        _plan_csv(project, stdout.getvalue()).read_bytes()
        == (FIXTURES / "upgrade-apply-with-force-accept" / "golden.csv").read_bytes()
    )
    assert (project / "examples/tests/test_smoke.py").is_file()
    assert not (project / "tests/test_smoke.py").exists()
    assert not (project / "tests/unit/test_drop.py").exists()

    managed = sidecar.read_sidecar(project)
    assert managed is not None
    sidecar.write_sidecar(project, managed)
    assert sidecar.read_sidecar(project) == managed

    recover_out, recover_err = StringIO(), StringIO()
    recover_code = main.run(
        ["--profile=minimal", "--upgrade", "--recover", "tests/unit/test_drop.py"],
        root=project,
        stdout=recover_out,
        stderr=recover_err,
    )
    assert recover_code == 2
    assert recover_out.getvalue() == ""
    assert_blocked_shape(recover_err.getvalue(), "path 'tests/unit/test_drop.py' is absent")
    assert not (project / "tests/unit/test_drop.py").exists()


def test_upgrade_apply_blocks_existing_move_target_without_accept(tmp_path: Path) -> None:
    project = _copy_fixture("upgrade-apply-with-force-accept", tmp_path)
    target = project / "examples" / "tests" / "test_smoke.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_existing() -> None:\n    assert True\n", encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(
        ["--profile=minimal", "--upgrade", "--apply", "--force", "--accept", "pyproject.toml"],
        root=project,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert_blocked_shape(stderr.getvalue(), "examples/tests/test_smoke.py was not accepted")
    assert (project / "tests" / "test_smoke.py").is_file()
    assert "test_existing" in target.read_text(encoding="utf-8")


def test_execute_plan_blocks_existing_move_target_without_accept(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_smoke.py"
    target = tmp_path / "examples" / "tests" / "test_smoke.py"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text("def test_smoke() -> None:\n    assert True\n", encoding="utf-8")
    target.write_text("def test_existing() -> None:\n    assert True\n", encoding="utf-8")
    row = disposition.DispositionRow(
        path="tests/test_smoke.py",
        target="examples/tests/test_smoke.py",
        disp="cheap",
        action="move",
        why="profile-layout correction",
        next="move",
    )
    managed = sidecar.build_sidecar(profile="minimal", ai_dev_cli_version=__version__, files=())

    with pytest.raises(apply.ApplyError) as exc_info:
        apply.execute_plan(
            tmp_path,
            (row,),
            profiles.load_shipped_profile("minimal"),
            managed,
            accepted_paths=frozenset(),
        )

    assert_blocked_shape(
        exc_info.value.message,
        "examples/tests/test_smoke.py is unmanaged and would be overwritten",
    )
    assert source.is_file()
    assert "test_existing" in target.read_text(encoding="utf-8")


def test_recover_adopts_user_restored_file(tmp_path: Path) -> None:
    sidecar.write_sidecar(
        tmp_path,
        sidecar.build_sidecar(profile="minimal", ai_dev_cli_version=__version__, files=()),
    )
    restored = tmp_path / "tests" / "unit" / "test_drop.py"
    restored.parent.mkdir(parents=True)
    restored.write_text("def test_restored() -> None:\n    assert True\n", encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()

    exit_code = main.run(
        [
            "--profile=minimal",
            "--upgrade",
            "--recover",
            "tests/unit/test_drop.py",
            "--force",
            "--accept",
            "tests/unit/test_drop.py",
        ],
        root=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert "Recovered tests/unit/test_drop.py." in stdout.getvalue()
    managed = sidecar.read_sidecar(tmp_path)
    assert managed is not None
    hashes = {item.path: item.sha256 for item in managed.files}
    assert hashes["tests/unit/test_drop.py"] == sidecar.sha256_path(restored)


def _copy_fixture(name: str, tmp_path: Path) -> Path:
    source = FIXTURES / name / "project"
    target = tmp_path / name
    copytree(source, target)
    return target


def _plan_csv(root: Path, stdout: str) -> Path:
    plan_line = next(line for line in stdout.splitlines() if line.startswith("Plan: "))
    return root / plan_line.removeprefix("Plan: ")
