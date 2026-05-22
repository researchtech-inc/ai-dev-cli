import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--echo", action="store", default="")


@pytest.fixture
def echo_value(request: pytest.FixtureRequest) -> str:
    return str(request.config.getoption("--echo"))
