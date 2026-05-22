"""Pytest lane-timeout plugin tests."""

from pathlib import Path
from typing import Any

import pytest
from _pytest.outcomes import Exit

from ai_dev_cli import _project, _pytest_plugin
from tests.support._assertions import assert_blocked_shape

FIXTURES = Path(__file__).parents[1] / "fixtures" / "pytest_plugin"


class FakeConfig:
    def __init__(self) -> None:
        self.ini_lines: list[tuple[str, str]] = []

    def addinivalue_line(self, name: str, line: str) -> None:
        self.ini_lines.append((name, line))


class FakeItem:
    def __init__(self, path: Path, marker: object | None = None) -> None:
        self.path = path
        self._marker = marker
        self.added_markers: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        return self._marker if name == "timeout" else None

    def add_marker(self, marker: Any) -> None:
        self.added_markers.append(marker)


def test_lane_derivation_from_path_adds_timeout_marker(monkeypatch) -> None:
    project = FIXTURES / "lane-derives-from-path"
    monkeypatch.chdir(project)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_pytest_plugin, "_require_pytest_timeout", lambda: None)
    config = FakeConfig()

    _pytest_plugin.pytest_configure(config)
    item = FakeItem(project / "tests" / "unit" / "test_x.py")
    _pytest_plugin.pytest_collection_modifyitems(config, [item])

    assert config.ini_lines == [("markers", _pytest_plugin.TIMEOUT_MARKER)]
    assert item.added_markers[0].mark.args == (30,)


def test_per_test_timeout_override_wins(monkeypatch) -> None:
    project = FIXTURES / "per-test-override-wins"
    monkeypatch.chdir(project)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_pytest_plugin, "_require_pytest_timeout", lambda: None)
    config = FakeConfig()
    existing_marker = object()

    _pytest_plugin.pytest_configure(config)
    item = FakeItem(project / "tests" / "unit" / "test_x.py", marker=existing_marker)
    _pytest_plugin.pytest_collection_modifyitems(config, [item])

    assert item.added_markers == []


def test_missing_pytest_timeout_fails_closed(monkeypatch, capsys) -> None:
    project = FIXTURES / "pytest-timeout-missing"
    monkeypatch.chdir(project)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_pytest_plugin.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(Exit) as exc_info:
        _pytest_plugin.pytest_configure(FakeConfig())

    assert exc_info.value.returncode == 2
    err = capsys.readouterr().err
    assert _pytest_plugin.PYTEST_TIMEOUT_BLOCK in err
    assert_blocked_shape(err)


def test_opt_out_short_circuits_plugin(monkeypatch) -> None:
    project = FIXTURES / "opt-out-disables-plugin"
    monkeypatch.chdir(project)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_pytest_plugin, "_require_pytest_timeout", _raise_if_called)
    config = FakeConfig()

    _pytest_plugin.pytest_configure(config)
    item = FakeItem(project / "tests" / "unit" / "test_x.py")
    _pytest_plugin.pytest_collection_modifyitems(config, [item])

    assert config.ini_lines == []
    assert item.added_markers == []


def _raise_if_called(*args: object, **kwargs: object) -> None:
    raise AssertionError("opt-out should return before pytest-timeout probing")
