"""Shared test assertions."""

import re


def assert_blocked_shape(message: str, expected_fragment: str | None = None) -> None:
    """Assert the required plain BLOCKED message shape."""
    text = message.strip()
    assert text.startswith("BLOCKED:")
    assert re.search(r"\bUse .+ instead\.$", text) is not None
    if expected_fragment is not None:
        assert expected_fragment in text
