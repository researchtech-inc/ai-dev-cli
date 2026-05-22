"""Rerun-failed selection tests."""

import json
import sqlite3
from pathlib import Path

from ai_dev_cli import _rerun
from tests.unit._helpers import write_file


def test_rerun_failed_reads_lastfailed_and_filters_by_scope(tmp_path: Path) -> None:
    write_file(tmp_path, "tests/unit/test_a.py", "def test_a() -> None:\n    assert True\n")
    write_file(tmp_path, "tests/integration/test_b.py", "def test_b() -> None:\n    assert True\n")
    _write_lastfailed(
        tmp_path,
        {
            "tests/unit/test_a.py::test_a": True,
            "tests/integration/test_b.py::test_b": True,
            "tests/unit/test_a.py::test_passed": False,
            "tests/unit/test_missing.py::test_missing": True,
        },
    )

    result = _rerun.compute_rerun_set(tmp_path, ("tests/unit",))

    assert result.should_run is True
    assert result.nodeids == ("tests/unit/test_a.py::test_a",)


def test_rerun_failed_returns_skip_when_no_recorded_failures_in_lane(tmp_path: Path, capsys) -> None:
    write_file(tmp_path, "tests/integration/test_b.py", "def test_b() -> None:\n    assert True\n")
    _write_lastfailed(tmp_path, {"tests/integration/test_b.py::test_b": True})

    result = _rerun.compute_rerun_set(tmp_path, ("tests/unit",))

    assert result.should_run is False
    assert result.nodeids == ()
    assert "no recorded failures in selected scope" in capsys.readouterr().out


def test_rerun_failed_uses_testmon_to_drop_stale_entries(monkeypatch, tmp_path: Path) -> None:
    stale = "tests/unit/test_a.py::test_stale"
    changed = "tests/unit/test_a.py::test_changed"
    write_file(tmp_path, "tests/unit/test_a.py", "def test_a() -> None:\n    assert True\n")
    write_file(tmp_path, "src/pkg/module.py", "VALUE = 1\n")
    _write_lastfailed(tmp_path, {stale: True, changed: True})
    _write_testmon(tmp_path, stale, "src/pkg/module.py")
    _write_testmon(tmp_path, changed, "src/pkg/module.py")
    previous = {stale: "same", changed: "old"}
    monkeypatch.setattr(_rerun, "load_failure_dependency_hash", lambda nodeid: previous[nodeid])
    monkeypatch.setattr(_rerun, "hash_tracked_files", lambda *filenames: "same")

    result = _rerun.compute_rerun_set(tmp_path, ("tests/unit",))

    assert result.should_run is True
    assert result.nodeids == (changed,)


def test_rerun_failed_skips_when_all_testmon_entries_are_stale(monkeypatch, tmp_path: Path, capsys) -> None:
    nodeid = "tests/unit/test_a.py::test_stale"
    write_file(tmp_path, "tests/unit/test_a.py", "def test_a() -> None:\n    assert True\n")
    _write_lastfailed(tmp_path, {nodeid: True})
    _write_testmon(tmp_path, nodeid, "src/pkg/module.py")
    monkeypatch.setattr(_rerun, "load_failure_dependency_hash", lambda selected: "same")
    monkeypatch.setattr(_rerun, "hash_tracked_files", lambda *filenames: "same")

    result = _rerun.compute_rerun_set(tmp_path, ("tests/unit",))

    assert result.should_run is False
    assert result.nodeids == ()
    assert "recorded failures are stale" in capsys.readouterr().out


def _write_lastfailed(root: Path, entries: dict[str, bool]) -> None:
    cache_path = root / ".pytest_cache" / "v" / "cache" / "lastfailed"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps(entries), encoding="utf-8")


def _write_testmon(root: Path, nodeid: str, filename: str) -> None:
    path = root / ".testmondata"
    with sqlite3.connect(path) as conn:
        conn.execute("create table if not exists test_execution (id integer primary key, test_name text)")
        conn.execute(
            "create table if not exists test_execution_file_fp (test_execution_id integer, fingerprint_id integer)"
        )
        conn.execute("create table if not exists file_fp (id integer primary key, filename text, fsha text)")
        cursor = conn.execute("insert into test_execution (test_name) values (?)", (nodeid,))
        test_id = int(cursor.lastrowid)
        cursor = conn.execute("insert into file_fp (filename, fsha) values (?, ?)", (filename, "recorded"))
        file_id = int(cursor.lastrowid)
        conn.execute(
            "insert into test_execution_file_fp (test_execution_id, fingerprint_id) values (?, ?)",
            (test_id, file_id),
        )
