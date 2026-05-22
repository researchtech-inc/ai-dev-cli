"""State hashing and cache invalidation tests."""

import json
import subprocess
from pathlib import Path

import ai_dev_cli
from ai_dev_cli import _runner, _state
from tests.unit._helpers import project_config, write_file


def test_hash_changes_when_ai_dev_cli_version_changes(monkeypatch, tmp_path: Path) -> None:
    write_file(tmp_path, "src/pkg/module.py", "VALUE = 1\n")
    cfg = project_config(tmp_path)
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    monkeypatch.setattr(_state.subprocess, "run", _failing_uv_run)

    monkeypatch.setattr(ai_dev_cli, "__version__", "0.1.0")
    first = _state.hash_tracked_files("src/pkg")
    monkeypatch.setattr(ai_dev_cli, "__version__", "0.1.1")

    assert _state.hash_tracked_files("src/pkg") != first


def test_hash_changes_when_resolved_dev_cli_config_changes(monkeypatch, tmp_path: Path) -> None:
    write_file(tmp_path, "src/pkg/module.py", "VALUE = 1\n")
    cfg = project_config(tmp_path, config_hash="resolved-a")
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    monkeypatch.setattr(_state.subprocess, "run", _failing_uv_run)

    first = _state.hash_tracked_files("src/pkg")
    cfg.config_hash = "resolved-b"

    assert _state.hash_tracked_files("src/pkg") != first


def test_hash_changes_when_source_or_test_fingerprint_changes(monkeypatch, tmp_path: Path) -> None:
    write_file(tmp_path, "src/pkg/module.py", "VALUE = 1\n")
    write_file(tmp_path, "tests/unit/test_module.py", "def test_value() -> None:\n    assert True\n")
    cfg = project_config(tmp_path)
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    monkeypatch.setattr(_state.subprocess, "run", _failing_uv_run)

    first = _state.hash_tracked_files("src/pkg", "tests/unit")
    write_file(tmp_path, "tests/unit/test_module.py", "def test_value() -> None:\n    assert 1\n")
    second = _state.hash_tracked_files("src/pkg", "tests/unit")
    write_file(tmp_path, "src/pkg/module.py", "VALUE = 2\n")

    assert second != first
    assert _state.hash_tracked_files("src/pkg", "tests/unit") != second


def test_cacheable_step_invalidates_when_tool_version_changes(monkeypatch, tmp_path: Path, capsys) -> None:
    write_file(tmp_path, "src/pkg/module.py", "VALUE = 1\n")
    write_file(tmp_path, "tests/unit/test_module.py", "def test_value() -> None:\n    assert True\n")
    cfg = project_config(tmp_path)
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)

    tool_version = {"stdout": "tool 1\n"}
    monkeypatch.setattr(_runner.subprocess, "run", lambda *args, **kwargs: _version_result(tool_version["stdout"]))
    calls: list[list[str]] = []

    def fake_run_command(cmd: list[str], *args: object, **kwargs: object) -> tuple[int, str, str, bool]:
        calls.append(cmd)
        write_file(tmp_path, "cached.log", "ok\n")
        return 0, "PASS  cacheable\n  Details: cached.log", "cached.log", False

    monkeypatch.setattr(_runner, "run_command", fake_run_command)

    assert _runner.run_check_step("lint", (("tool", "check"),), cacheable=True) == 0
    assert _runner.run_check_step("lint", (("tool", "check"),), cacheable=True) == 0
    tool_version["stdout"] = "tool 2\n"
    assert _runner.run_check_step("lint", (("tool", "check"),), cacheable=True) == 0

    assert len(calls) == 2
    assert "SKIP  lint (cached)" in capsys.readouterr().out


def test_state_schema_mismatch_rebuilds_and_save_writes_schema(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path)
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    cfg.runs_dir.mkdir(parents=True)
    cfg.state_file.write_text(
        json.dumps({
            "schema_version": 999,
            "tool_version": ai_dev_cli.__version__,
            "config_hash": cfg.config_hash,
            "runs": {
                "unit": {
                    "command": "dev test",
                    "timestamp": "2026-05-22T00:00:00Z",
                    "exit_code": 0,
                    "summary": "old",
                    "log_file": "old.log",
                    "file_hash": "same",
                }
            },
        }),
        encoding="utf-8",
    )

    assert _state.check_idempotency("unit", "same") is None

    _state.save_run_state("unit", "dev test", 0, "ok", "new.log", "same")
    data = json.loads(cfg.state_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == _state.STATE_SCHEMA_VERSION
    assert data["tool_version"] == ai_dev_cli.__version__
    assert data["config_hash"] == cfg.config_hash


def _failing_uv_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")


def _version_result(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=("tool", "--version"), returncode=0, stdout=stdout, stderr="")
