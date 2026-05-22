"""Lane configuration tests."""

from types import SimpleNamespace

from ai_dev_cli import _lane


def test_load_lanes_reads_configured_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        _lane,
        "load_config",
        lambda: SimpleNamespace(
            dev_cli_config={
                "lanes": {
                    "unit": {
                        "paths": ["tests/fast"],
                        "timeout": 9,
                        "xdist": ["-n", "2"],
                        "testmon": False,
                        "agent_allowed": False,
                        "blast_radius_ratio": 0.25,
                        "slow_threshold": 3.5,
                    }
                }
            }
        ),
    )

    lanes = _lane.load_lanes()
    unit = lanes["unit"]

    assert unit.paths == ("tests/fast",)
    assert unit.timeout == 9
    assert unit.xdist == ("-n", "2")
    assert unit.testmon is False
    assert unit.agent_allowed is False
    assert unit.blast_radius_ratio == 0.25
    assert unit.slow_threshold == 3.5


def test_only_configured_lane_keys_are_loaded(monkeypatch) -> None:
    monkeypatch.setattr(
        _lane,
        "load_config",
        lambda: SimpleNamespace(
            dev_cli_config={
                "lanes": {
                    "unit": {
                        "paths": ["tests/unit_fast"],
                        "timeout": 12,
                    }
                }
            }
        ),
    )

    lanes = _lane.load_lanes()

    assert tuple(lanes) == ("unit",)
    assert lanes["unit"].paths == ("tests/unit_fast",)
    assert lanes["unit"].xdist == _lane.LANE_XDIST_ARGS["unit"]


def test_no_lanes_table_means_no_configured_lanes(monkeypatch) -> None:
    monkeypatch.setattr(_lane, "load_config", lambda: SimpleNamespace(dev_cli_config={}))

    lanes = _lane.load_lanes()

    assert lanes == {}


def test_slow_threshold_defaults_are_lane_specific(monkeypatch) -> None:
    monkeypatch.setattr(
        _lane,
        "load_config",
        lambda: SimpleNamespace(
            dev_cli_config={
                "lanes": {
                    "unit": {"paths": ["tests/unit"]},
                    "integration": {"paths": ["tests/integration"]},
                    "qualification": {"paths": ["tests/qualification"]},
                }
            }
        ),
    )

    lanes = _lane.load_lanes()

    assert lanes["unit"].slow_threshold == 30.0
    assert lanes["integration"].slow_threshold == 120.0
    assert lanes["qualification"].slow_threshold is None
