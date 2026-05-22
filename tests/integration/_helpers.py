"""Shared integration-test helpers for subprocess e2e coverage."""

import hashlib
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from ai_dev_cli.bootstrap import sidecar

ARTIFACT_ROOT = Path(__file__).resolve().parents[2]
SRC_PATH = ARTIFACT_ROOT / "src"
FIXTURES_ROOT = ARTIFACT_ROOT / "tests" / "fixtures" / "integration"
DEV_MODULE = "ai_dev_cli.main"
HOOK_MODULE = "ai_dev_cli.hook"


def run_module(
    module: str,
    args: tuple[str, ...],
    *,
    cwd: Path,
    input_text: str | None = None,
    extra_env: dict[str, str] | None = None,
    extra_pythonpath: tuple[Path, ...] = (),
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run an artifact module in a subprocess with PYTHONPATH pointed at src."""
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        env=module_env(extra_env=extra_env, extra_pythonpath=extra_pythonpath),
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_dev(
    args: tuple[str, ...],
    *,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    """Run python -m ai_dev_cli.main in a subprocess."""
    return run_module(DEV_MODULE, args, cwd=cwd, extra_env=extra_env, timeout=timeout)


def run_hook(
    event: str,
    *,
    cwd: Path,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run python -m ai_dev_cli.hook in a subprocess."""
    return run_module(HOOK_MODULE, (), cwd=cwd, input_text=event, timeout=timeout)


def module_env(
    *,
    extra_env: dict[str, str] | None = None,
    extra_pythonpath: tuple[Path, ...] = (),
) -> dict[str, str]:
    """Return an environment that imports the artifact source tree."""
    env = os.environ.copy()
    pythonpath = [*(path.as_posix() for path in extra_pythonpath), SRC_PATH.as_posix()]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    if extra_env is not None:
        env.update(extra_env)
    return env


def copy_integration_fixture(name: str, tmp_path: Path) -> Path:
    """Copy one integration fixture tree to tmp_path."""
    source = FIXTURES_ROOT / name
    target = tmp_path / name
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".keep"))
    return target


def copy_project_fixture(source: Path, tmp_path: Path) -> Path:
    """Copy an existing fixture project tree to tmp_path."""
    target = tmp_path / source.name
    shutil.copytree(source, target)
    return target


def assert_tree_matches_golden(project: Path, golden: Path) -> None:
    """Assert every generated file matches the golden tree byte-for-byte."""
    expected = _relative_files(golden)
    actual = _relative_files(project, excluded={sidecar.SIDECAR_NAME})
    assert actual == expected
    for relative in expected:
        assert (project / relative).read_bytes() == (golden / relative).read_bytes()


def assert_sidecar_tracks(project: Path, profile: str, paths: tuple[str, ...]) -> None:
    """Assert the managed sidecar lists paths with current SHA256 values."""
    data = tomllib.loads((project / sidecar.SIDECAR_NAME).read_text(encoding="utf-8"))
    files = {item["path"]: item for item in data["files"]}
    assert data["profile"] == profile
    assert set(paths) <= set(files)
    for relative in paths:
        digest = hashlib.sha256((project / relative).read_bytes()).hexdigest()
        assert files[relative]["sha256"] == digest
        assert files[relative]["template_id"].startswith(f"{profile}/")
        assert files[relative]["template_version"] == 1


def remove_sidecar_path(project: Path, relative: str) -> None:
    """Remove one path from the managed sidecar while leaving the file in place."""
    managed = sidecar.read_sidecar(project)
    assert managed is not None
    sidecar.write_sidecar(
        project,
        sidecar.Sidecar(
            profile=managed.profile,
            ai_dev_cli_version=managed.ai_dev_cli_version,
            written_at=managed.written_at,
            files=tuple(item for item in managed.files if item.path != relative),
        ),
    )


def sidecar_hash(project: Path, relative: str) -> str:
    """Return the sidecar SHA256 recorded for one path."""
    managed = sidecar.read_sidecar(project)
    assert managed is not None
    matches = [item.sha256 for item in managed.files if item.path == relative]
    assert matches
    return matches[0]


def file_sha256(path: Path) -> str:
    """Return the SHA256 digest for a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_files(root: Path, *, excluded: set[str] | None = None) -> tuple[str, ...]:
    excluded_paths = excluded or set()
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.relative_to(root).as_posix() not in excluded_paths
        )
    )
