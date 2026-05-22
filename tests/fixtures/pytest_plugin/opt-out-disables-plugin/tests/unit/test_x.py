import pytest


def test_opt_out_leaves_timeout_marker_unmodified(request: pytest.FixtureRequest) -> None:
    assert request.node.get_closest_marker("timeout") is None
