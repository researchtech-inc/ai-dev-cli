"""Runner and dev-test orchestration tests."""

import argparse
import json
import subprocess
from pathlib import Path

from ai_dev_cli import _runner, main
from ai_dev_cli._lane import LaneConfig
from tests.unit._helpers import project_config


def test_run_check_step_honors_cacheable_false(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path)
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    monkeypatch.setattr("ai_dev_cli._state.hash_tracked_files", lambda *paths: "hash")
    monkeypatch.setattr("ai_dev_cli._state.load_step_cache", _raise_if_called)
    monkeypatch.setattr("ai_dev_cli._state.save_step_cache", _raise_if_called)
    monkeypatch.setattr(_runner, "_tool_version_for_commands", _raise_if_called)
    calls: list[list[str]] = []

    def fake_run_command(cmd: list[str], *args: object, **kwargs: object) -> tuple[int, str, str, bool]:
        calls.append(cmd)
        return 0, "PASS", "log.txt", False

    monkeypatch.setattr(_runner, "run_command", fake_run_command)

    assert _runner.run_check_step("custom", (("tool", "arg"),), cacheable=False) == 0
    assert calls == [["tool", "arg"]]


def test_run_check_step_applies_per_step_timeout(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path, step_timeouts={"lint": 17})
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    monkeypatch.setattr("ai_dev_cli._state.hash_tracked_files", lambda *paths: "hash")
    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], *args: object, **kwargs: object) -> tuple[int, str, str, bool]:
        captured.update(kwargs)
        return 0, "PASS", "log.txt", False

    monkeypatch.setattr(_runner, "run_command", fake_run_command)

    assert _runner.run_check_step("lint", (("ruff", "check"),), cacheable=False) == 0
    assert captured["timeout_seconds"] == 17


def test_pytest_summary_uses_supplied_slow_threshold(tmp_path: Path) -> None:
    report = tmp_path / "report.jsonl"
    report.write_text(
        "\n".join((
            json.dumps({
                "$report_type": "TestReport",
                "when": "call",
                "outcome": "passed",
                "duration": 2.5,
                "start": 1.0,
                "stop": 3.5,
                "nodeid": "tests/unit/test_slow.py::test_slow",
            }),
            "",
        )),
        encoding="utf-8",
    )

    summary, _ = _runner._summarize_pytest_report(report, 0, "log.txt", slow_threshold=2.0)
    quiet_summary, _ = _runner._summarize_pytest_report(report, 0, "log.txt", slow_threshold=3.0)

    assert "Slow tests (>2s):" in summary
    assert "tests/unit/test_slow.py::test_slow (2.5s)" in summary
    assert "Slow tests" not in quiet_summary


def test_dev_test_lane_paths_skip_wrapper_timeout(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path, source_packages=[], source_to_test={})
    lane = LaneConfig(
        name="unit",
        paths=("tests/unit",),
        xdist=("-n", "auto"),
        timeout=30,
        slow_threshold=4.0,
        testmon=True,
        agent_allowed=True,
        blast_radius_ratio=None,
    )
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "load_lanes", lambda: {"unit": lane})
    monkeypatch.setattr(main, "hash_tracked_files", lambda *paths: "hash")
    monkeypatch.setattr(main, "check_idempotency", lambda *args: None)
    monkeypatch.setattr(main, "save_run_state", lambda *args: None)
    monkeypatch.setattr(main, "get_source_dirs_for_test_dirs", lambda paths: [])
    captured: dict[str, object] = {}

    def fake_run_command(cmd: list[str], *args: object, **kwargs: object) -> tuple[int, str, str, bool]:
        captured.update(kwargs)
        return 0, "PASS", "log.txt", False

    monkeypatch.setattr(main, "run_command", fake_run_command)
    args = argparse.Namespace(scope=None, lane="unit", rerun_failed=False)

    assert main.cmd_test(args) == 0
    assert captured.get("timeout_seconds") is None
    assert captured["slow_threshold"] == 4.0


def test_run_command_returns_documented_exit_codes(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path)
    monkeypatch.setattr("ai_dev_cli._project.load_config", lambda: cfg)
    monkeypatch.setattr(_runner, "_cleanup_done", True)

    def fake_run(cmd: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "timeout":
            raise subprocess.TimeoutExpired(cmd, 7)
        if cmd[0] == "missing":
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(args=cmd, returncode=int(cmd[0]), stdout="", stderr="")

    monkeypatch.setattr(_runner.subprocess, "run", fake_run)

    assert _runner.run_command(["0"], "zero")[0] == 0
    assert _runner.run_command(["1"], "one")[0] == 1
    assert _runner.run_command(["2"], "two")[0] == 2
    assert _runner.run_command(["timeout"], "timeout", timeout_seconds=7)[0] == 124
    assert _runner.run_command(["missing"], "missing")[0] == 127


def test_failure_summaries_include_next_step(tmp_path: Path) -> None:
    report = tmp_path / "report.jsonl"
    report.write_text("", encoding="utf-8")

    collection_summary, _ = _runner._summarize_pytest_report(report, 2, "log.txt")
    generic_summary = _runner._summarize_generic(
        subprocess.CompletedProcess(args=("tool",), returncode=1),
        "custom",
        "log.txt",
    )

    assert "Next step:" in collection_summary
    assert "Next step:" in generic_summary


def _raise_if_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("unexpected cache boundary call")
