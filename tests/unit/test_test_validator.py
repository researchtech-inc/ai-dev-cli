"""Universal test-validator tests."""

import re
from pathlib import Path

from ai_dev_cli import _test_validator
from tests.unit._helpers import write_file


def test_validate_flags_universal_ast_rules(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "tests/unit/test_live.py",
        "from openai import OpenAI\n\n\ndef test_live() -> None:\n    OpenAI().responses.create()\n",
    )
    write_file(
        tmp_path,
        "tests/unit/test_sleep.py",
        "import asyncio\n\n\nasync def test_sleep() -> None:\n    await asyncio.sleep(1)\n",
    )
    write_file(
        tmp_path,
        "tests/unit/test_parametrize.py",
        "import pytest\n\n\n@pytest.mark.parametrize('x', [1])\n@pytest.mark.parametrize('y', [2])\n"
        "def test_matrix(x: int, y: int) -> None:\n    assert x + y\n",
    )
    write_file(
        tmp_path,
        "tests/unit/test_path.py",
        'from pathlib import Path\n\n\ndef test_path() -> None:\n    Path(".")\n',
    )
    write_file(
        tmp_path,
        "tests/unit/test_tokens.py",
        "def call(**kwargs: object) -> None:\n    pass\n\n\ndef test_tokens() -> None:\n"
        "    call(max_tokens=1)\n    call(max_output_tokens=1)\n    call(max_completion_tokens=1)\n",
    )
    write_file(
        tmp_path,
        "tests/unit/conftest.py",
        "def pytest_pyfunc_call() -> None:\n    pass\n",
    )

    violations = _test_validator.validate(
        tmp_path,
        ("tests",),
        unit_roots=("tests/unit",),
        qualification_roots=("tests/qualification",),
    )
    rule_ids = [violation.rule_id for violation in violations]

    assert "LIVE-LLM-UNIT" in rule_ids
    assert "ASYNC-SLEEP" in rule_ids
    assert "STACKED-PARAMETRIZE" in rule_ids
    assert "PATH-DOT" in rule_ids
    assert "PYTEST-HOOK" in rule_ids
    assert rule_ids.count("TOKEN-CAP") == 3


def test_qualification_lane_allows_long_asyncio_sleep(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "tests/qualification/test_sleep.py",
        "import asyncio\n\n\nasync def test_sleep() -> None:\n    await asyncio.sleep(5)\n",
    )

    violations = _test_validator.validate(
        tmp_path,
        ("tests",),
        unit_roots=("tests/unit",),
        qualification_roots=("tests/qualification",),
    )

    assert violations == []


def test_live_llm_rule_is_limited_to_unit_roots(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        "tests/integration/test_live.py",
        "from openai import AsyncOpenAI\n\n\nasync def test_live() -> None:\n"
        "    await AsyncOpenAI().chat.completions.create()\n",
    )

    violations = _test_validator.validate(
        tmp_path,
        ("tests",),
        unit_roots=("tests/unit",),
        qualification_roots=("tests/qualification",),
    )

    assert [violation.rule_id for violation in violations] == []


def test_validator_contains_no_framework_specific_symbols() -> None:
    source = Path(_test_validator.__file__).read_text(encoding="utf-8")

    for symbol in ("AIModel", "Conversation", "send", "send_structured", "generate"):
        assert re.search(rf"\b{re.escape(symbol)}\b", source) is None
