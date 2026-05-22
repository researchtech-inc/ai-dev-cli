import pytest


@pytest.mark.timeout(5)
def test_explicit_timeout_marker_is_preserved(request: pytest.FixtureRequest) -> None:
    marker = request.node.get_closest_marker("timeout")
    assert marker is not None
    assert marker.args == (5,)
