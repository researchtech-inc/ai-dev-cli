"""Generic pytest configuration for ai-dev-cli."""

from collections.abc import Iterator

import pytest

collect_ignore_glob = ["fixtures/**"]


def pytest_configure(config: pytest.Config) -> None:
    """Register the timeout marker before the lane-timeout plugin lands."""
    config.addinivalue_line(
        "markers",
        "timeout(seconds): per-test timeout; lane defaults are added by the ai_dev_cli pytest plugin once implemented",
    )


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> Iterator[None]:
    """Keep xdist workers aligned when testmon selects a subset."""
    yield
    items.sort(key=lambda item: item.nodeid)
