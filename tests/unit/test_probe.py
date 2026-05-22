"""Probe command tests."""

import argparse
from pathlib import Path
from typing import Any

from ai_dev_cli import probe
from tests.support._assertions import assert_blocked_shape
from tests.unit._helpers import project_config, write_file


def test_missing_probe_blocks(monkeypatch, tmp_path: Path, capsys) -> None:
    cfg = project_config(tmp_path, dev_cli_config={})
    monkeypatch.setattr(probe, "load_config", lambda: cfg)

    exit_code = probe.cmd_probe(argparse.Namespace(name="smoke", pytest_args=[]))

    assert exit_code == 2
    assert_blocked_shape(capsys.readouterr().err, "Use [tool.dev-cli.probes.smoke] instead.")


def test_disabled_probe_blocks(monkeypatch, tmp_path: Path, capsys) -> None:
    cfg = project_config(tmp_path, dev_cli_config={"probes": {"smoke": {"enabled": False}}})
    monkeypatch.setattr(probe, "load_config", lambda: cfg)

    exit_code = probe.cmd_probe(argparse.Namespace(name="smoke", pytest_args=[]))

    assert exit_code == 2
    assert_blocked_shape(capsys.readouterr().err, "Use [tool.dev-cli.probes.smoke] instead.")


def test_missing_target_blocks(monkeypatch, tmp_path: Path, capsys) -> None:
    cfg = project_config(tmp_path, dev_cli_config={"probes": {"smoke": {"enabled": True}}})
    monkeypatch.setattr(probe, "load_config", lambda: cfg)

    exit_code = probe.cmd_probe(argparse.Namespace(name="smoke", pytest_args=[]))

    assert exit_code == 2
    assert_blocked_shape(capsys.readouterr().err, "probe 'smoke' has no target")


def test_nonexistent_target_blocks(monkeypatch, tmp_path: Path, capsys) -> None:
    cfg = project_config(
        tmp_path,
        dev_cli_config={"probes": {"smoke": {"enabled": True, "target": "tests/probe"}}},
    )
    monkeypatch.setattr(probe, "load_config", lambda: cfg)

    exit_code = probe.cmd_probe(argparse.Namespace(name="smoke", pytest_args=[]))

    assert exit_code == 2
    assert_blocked_shape(capsys.readouterr().err, "probe target does not exist: tests/probe")


def test_valid_probe_builds_pytest_command_and_appends_args_verbatim(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    write_file(tmp_path, "tests/probe/test_smoke.py", "def test_smoke() -> None:\n    assert True\n")
    cfg = project_config(
        tmp_path,
        dev_cli_config={
            "probes": {
                "smoke": {
                    "enabled": True,
                    "target": "tests/probe",
                    "agent_allowed": True,
                    "timeout": 45,
                }
            }
        },
    )
    monkeypatch.setattr(probe, "load_config", lambda: cfg)
    captured: dict[str, Any] = {}

    def fake_run_command(cmd: list[str], *args: object, **kwargs: object) -> tuple[int, str, str, bool]:
        captured["cmd"] = cmd
        captured["args"] = args
        captured["kwargs"] = kwargs
        return 0, "PASS  test\n  Details: .tmp/dev-runs/probe-smoke.log", ".tmp/dev-runs/probe-smoke.log", False

    monkeypatch.setattr(probe, "run_command", fake_run_command)
    pytest_args = ["--echo=hello", "-k", "smoke and not slow"]

    exit_code = probe.cmd_probe(argparse.Namespace(name="smoke", pytest_args=pytest_args))

    assert exit_code == 0
    assert captured["cmd"] == [
        "pytest",
        "--tb=short",
        "--no-header",
        "-q",
        "tests/probe",
        "--echo=hello",
        "-k",
        "smoke and not slow",
    ]
    assert captured["kwargs"] == {
        "label": "probe-smoke",
        "log_suffix": "probe-smoke",
        "use_reportlog": True,
        "timeout_seconds": 45,
    }
    assert capsys.readouterr().out == "PASS  test\n  Details: .tmp/dev-runs/probe-smoke.log\n"
