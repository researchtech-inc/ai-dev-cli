"""Pytest plugin for lane-owned per-test timeouts."""

import importlib.util
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Never

import pytest

from ai_dev_cli._lane import load_lanes
from ai_dev_cli._project import load_config

PYTEST_TIMEOUT_BLOCK = (
    "BLOCKED: ai_dev_cli pytest plugin requires pytest_timeout. Use 'pip install pytest-timeout' instead."
)
PYPROJECT_BLOCK = (
    "BLOCKED: ai_dev_cli pytest plugin could not read pyproject.toml. Use a readable pyproject.toml instead."
)
TIMEOUT_MARKER = "timeout(seconds): per-test timeout; lane defaults are added by the ai_dev_cli pytest plugin"
_PLUGIN_STATE_ATTR = "_ai_dev_cli_pytest_plugin_state"


@dataclass(frozen=True, slots=True)
class _LaneTimeout:
    paths: tuple[str, ...]
    timeout: int


@dataclass(frozen=True, slots=True)
class _PluginState:
    repo_root: Path
    lane_timeouts: tuple[_LaneTimeout, ...]


def pytest_configure(config: pytest.Config) -> None:
    """Load project lane policy and prepare timeout marker application."""
    try:
        project_config = load_config(force_reload=True)
    except (
        OSError,
        tomllib.TOMLDecodeError,
    ):
        _exit_blocked(PYPROJECT_BLOCK)

    cli_config = project_config.dev_cli_config
    if not cli_config:
        return
    if _is_disabled(cli_config.get("pytest_plugin")):
        return

    lane_timeouts = _load_lane_timeouts(cli_config)
    if not lane_timeouts:
        return

    _require_pytest_timeout()
    config.addinivalue_line("markers", TIMEOUT_MARKER)
    setattr(config, _PLUGIN_STATE_ATTR, _PluginState(project_config.repo_root, lane_timeouts))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Add lane timeout markers to collected items that do not define their own timeout."""
    state = _plugin_state(config)
    if state is None:
        return

    for item in items:
        if item.get_closest_marker("timeout") is not None:
            continue

        path_text = _item_path(item, state.repo_root)
        timeout = _timeout_for_path(path_text, state.lane_timeouts)
        if timeout is not None:
            item.add_marker(pytest.mark.timeout(timeout))


def _plugin_state(config: pytest.Config) -> _PluginState | None:
    state = getattr(config, _PLUGIN_STATE_ATTR, None)
    return state if isinstance(state, _PluginState) else None


def _is_disabled(raw_pytest_plugin: object) -> bool:
    return isinstance(raw_pytest_plugin, dict) and raw_pytest_plugin.get("enabled") is False


def _require_pytest_timeout() -> None:
    if importlib.util.find_spec("pytest_timeout") is None:
        _exit_blocked(PYTEST_TIMEOUT_BLOCK)


def _load_lane_timeouts(cli_config: dict[str, object]) -> tuple[_LaneTimeout, ...]:
    raw_lanes = cli_config.get("lanes")
    if not isinstance(raw_lanes, dict):
        return ()

    return tuple(
        _LaneTimeout(paths=lane.paths, timeout=lane.timeout)
        for lane in load_lanes().values()
        if lane.timeout is not None
    )


def _item_path(item: pytest.Item, repo_root: Path) -> str:
    try:
        return item.path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return item.path.as_posix()


def _timeout_for_path(path_text: str, lane_timeouts: tuple[_LaneTimeout, ...]) -> int | None:
    for lane in lane_timeouts:
        if any(_path_matches(path_text, root) for root in lane.paths):
            return lane.timeout
    return None


def _path_matches(path_text: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return path_text == normalized or path_text.startswith(f"{normalized}/")


def _exit_blocked(message: str) -> Never:
    sys.stderr.write(f"{message}\n")
    pytest.exit("", returncode=2)
