"""Shared lane policy for the dev CLI and pytest hooks."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_dev_cli._project import load_config

LANES = ("unit", "integration", "qualification")

LANE_PATHS: dict[str, tuple[str, ...]] = {
    "unit": ("tests/unit",),
    "integration": ("tests/integration",),
    "qualification": ("tests/qualification",),
}

LANE_XDIST_ARGS: dict[str, tuple[str, ...]] = {
    "unit": ("-n", "auto", "--dist", "worksteal"),
    "integration": ("-n", "4", "--dist", "loadfile"),
    "qualification": ("-n", "2"),
}

LANE_TIMEOUTS: dict[str, int] = {
    "unit": 30,
    "integration": 180,
    "qualification": 900,
}

LANE_SLOW_THRESHOLDS: dict[str, float | None] = {
    "unit": 30.0,
    "integration": 120.0,
    "qualification": None,
}

TESTMON_LANES = frozenset({"unit", "integration"})


@dataclass(frozen=True, slots=True)
class LaneConfig:
    """Resolved lane configuration."""

    name: str
    paths: tuple[str, ...]
    xdist: tuple[str, ...]
    timeout: int | None
    slow_threshold: float | None
    testmon: bool
    agent_allowed: bool
    blast_radius_ratio: float | None


def _default_lanes() -> dict[str, LaneConfig]:
    return {
        lane: LaneConfig(
            name=lane,
            paths=LANE_PATHS[lane],
            xdist=LANE_XDIST_ARGS.get(lane, ()),
            timeout=LANE_TIMEOUTS.get(lane),
            slow_threshold=LANE_SLOW_THRESHOLDS.get(lane),
            testmon=lane in TESTMON_LANES,
            agent_allowed=True,
            blast_radius_ratio=None,
        )
        for lane in LANES
    }


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _read_dev_cli_config() -> dict[str, Any]:
    return load_config().dev_cli_config


def load_lanes() -> dict[str, LaneConfig]:
    """Load lane configuration from [tool.dev-cli.lanes]."""
    cli_config = _read_dev_cli_config()
    raw_lanes = cli_config.get("lanes")
    if not isinstance(raw_lanes, dict):
        return {}
    if not raw_lanes:
        return {}

    default_lanes = _default_lanes()
    lanes: dict[str, LaneConfig] = {}
    for name, raw in raw_lanes.items():
        if not isinstance(name, str) or not isinstance(raw, dict):
            continue
        base = default_lanes.get(name)
        paths = _as_str_tuple(raw.get("paths")) or (base.paths if base is not None else ())
        if not paths:
            continue
        timeout = raw.get("timeout", base.timeout if base is not None else None)
        slow_threshold = raw.get(
            "slow_threshold",
            base.slow_threshold if base is not None else LANE_SLOW_THRESHOLDS.get(name),
        )
        blast_radius_ratio = raw.get("blast_radius_ratio")
        lanes[name] = LaneConfig(
            name=name,
            paths=paths,
            xdist=_as_str_tuple(raw.get("xdist")) or (base.xdist if base is not None else ()),
            timeout=timeout if isinstance(timeout, int) else None,
            slow_threshold=float(slow_threshold) if isinstance(slow_threshold, int | float) else None,
            testmon=bool(raw.get("testmon", base.testmon if base is not None else False)),
            agent_allowed=bool(raw.get("agent_allowed", base.agent_allowed if base is not None else True)),
            blast_radius_ratio=blast_radius_ratio if isinstance(blast_radius_ratio, int | float) else None,
        )
    return lanes


def _path_matches(path_text: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return path_text == normalized or path_text.startswith(f"{normalized}/")


def _configured_special_lane(path_text: str, cli_config: dict[str, Any]) -> str | None:
    examples = cli_config.get("examples")
    if isinstance(examples, dict):
        path_lane = examples.get("path_lane")
        if isinstance(path_lane, str):
            for key in ("static_path", "smoke_path", "live_path"):
                configured_path = examples.get(key)
                if isinstance(configured_path, str) and _path_matches(path_text, configured_path):
                    return path_lane

    benchmark = cli_config.get("benchmark")
    if isinstance(benchmark, dict):
        path_lane = benchmark.get("path_lane")
        target = benchmark.get("target")
        if isinstance(path_lane, str) and isinstance(target, str) and _path_matches(path_text, target):
            return path_lane

    return None


def lane_from_path(path: str | Path) -> str | None:
    """Return the lane encoded by a repository-relative path."""
    path_text = Path(path).as_posix()
    for lane, config in load_lanes().items():
        if any(_path_matches(path_text, root) for root in config.paths):
            return lane

    configured_lane = _configured_special_lane(path_text, _read_dev_cli_config())
    if configured_lane is not None:
        return configured_lane

    return None


def lane_timeout_for_path(path: str | Path) -> int | None:
    """Return the configured timeout for the lane containing a path."""
    lane = lane_from_path(path)
    if lane is None:
        return None
    config = load_lanes().get(lane)
    return config.timeout if config is not None else None
