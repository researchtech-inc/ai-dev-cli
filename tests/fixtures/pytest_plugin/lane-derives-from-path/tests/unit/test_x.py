import pytest


def test_lane_timeout_marker_is_applied(request: pytest.FixtureRequest) -> None:
    marker = request.node.get_closest_marker("timeout")
    assert marker is not None
    assert marker.args == (30,)
