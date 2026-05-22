"""Test scope detection tests."""

import argparse
from pathlib import Path

import pytest

from ai_dev_cli import _scope, main
from ai_dev_cli._lane import LaneConfig
from tests.support._assertions import assert_blocked_shape
from tests.unit._helpers import project_config, write_file


def test_git_diff_scoping_maps_source_and_changed_tests(monkeypatch, tmp_path: Path) -> None:
    write_file(tmp_path, "tests/unit/api/test_service.py", "def test_service() -> None:\n    assert True\n")
    cfg = project_config(
        tmp_path,
        test_roots=["tests"],
        source_to_test={"src/pkg/api": ["tests/unit/api"]},
    )
    monkeypatch.setattr(_scope, "load_config", lambda: cfg)
    monkeypatch.setattr(
        _scope,
        "git_changed_files",
        lambda: ["src/pkg/api/service.py", "tests/unit/api/test_service.py"],
    )
    monkeypatch.setattr(_scope, "load_lanes", lambda: {"unit": _lane("unit", ("tests/unit",))})

    test_dirs, source_dirs = _scope.detect_test_scope()

    assert test_dirs == ["tests/unit/api"]
    assert source_dirs == ["src/pkg/api"]


def test_blast_radius_guard_blocks_auto_scope_only(
    monkeypatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("a", "b", "c"):
        write_file(tmp_path, f"tests/integration/{name}/test_x.py", "def test_x() -> None:\n    assert True\n")
    cfg = project_config(tmp_path, test_roots=["tests"], scope_aliases={})
    lane = _lane("integration", ("tests/integration",), blast_radius_ratio=0.5)
    monkeypatch.setattr(_scope, "load_config", lambda: cfg)
    monkeypatch.setattr(_scope, "load_lanes", lambda: {"integration": lane})
    monkeypatch.setattr(
        _scope,
        "lane_from_path",
        lambda path: "integration" if str(path).startswith("tests/integration") else None,
    )
    monkeypatch.setattr(
        _scope,
        "detect_test_scope",
        lambda: (["tests/integration/a", "tests/integration/b"], []),
    )

    with pytest.raises(SystemExit) as exc_info:
        _scope.resolve_scope(None)

    assert exc_info.value.code == 2
    assert_blocked_shape(capsys.readouterr().err, "auto scope touches too much of the integration lane")
    assert _scope.resolve_scope("tests/integration/a") == ["tests/integration/a"]


def test_lane_selection_bypasses_auto_scope_blast_radius(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path)
    lane = _lane("integration", ("tests/integration",), blast_radius_ratio=0.1)
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "load_lanes", lambda: {"integration": lane})
    monkeypatch.setattr(main, "resolve_scope", _raise_if_called)
    args = argparse.Namespace(scope=None, lane="integration")

    assert main._build_test_selection(args) == ("integration", "lane-integration", ["tests/integration"])


def test_no_configured_lanes_falls_back_to_test_roots(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path, test_roots=["tests"])
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "load_lanes", dict)
    monkeypatch.setattr(main, "resolve_scope", lambda scope: [])
    args = argparse.Namespace(scope=None, lane=None)

    assert main._build_test_selection(args) == (None, "test-roots", ["tests"])


def test_lane_argument_is_rejected_when_no_lanes_are_configured(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(tmp_path, test_roots=["tests"])
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "load_lanes", dict)
    args = argparse.Namespace(scope=None, lane="unit")

    with pytest.raises(SystemExit) as exc_info:
        main._build_test_selection(args)

    assert exc_info.value.code == 2


def test_conftest_change_expands_to_all_test_roots(monkeypatch, tmp_path: Path) -> None:
    cfg = project_config(
        tmp_path,
        test_roots=["tests", "examples/tests"],
        source_to_test={"src/pkg": ["tests/unit"]},
    )
    monkeypatch.setattr(_scope, "load_config", lambda: cfg)
    monkeypatch.setattr(_scope, "git_changed_files", lambda: ["src/pkg/service.py", "tests/conftest.py"])

    test_dirs, source_dirs = _scope.detect_test_scope()

    assert test_dirs == ["tests", "examples/tests"]
    assert source_dirs == ["src/pkg"]


def _lane(
    name: str,
    paths: tuple[str, ...],
    *,
    blast_radius_ratio: float | None = None,
) -> LaneConfig:
    return LaneConfig(
        name=name,
        paths=paths,
        xdist=(),
        timeout=None,
        slow_threshold=None,
        testmon=False,
        agent_allowed=True,
        blast_radius_ratio=blast_radius_ratio,
    )


def _raise_if_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("auto scope should not be resolved for explicit lane")
