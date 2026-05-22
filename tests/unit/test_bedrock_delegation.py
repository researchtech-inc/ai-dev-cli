"""Bedrock quality-surface delegation tests."""

from pathlib import Path

import pytest

from ai_dev_cli._bedrock import delegation
from tests.unit._helpers import write_file


def test_scanner_blocks_raw_quality_tools_in_delegation_surfaces(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        ".pre-commit-config.yaml",
        """
repos:
  - repo: local
    hooks:
      - id: pytest
        entry: ruff check .
""",
    )
    write_file(
        tmp_path,
        "Makefile",
        "test:\n\tpython -m basedpyright\nlint: ; pyright\n",
    )
    write_file(
        tmp_path,
        ".github/workflows/ci.yml",
        """
name: ci
jobs:
  quality:
    steps:
      - run: |
          python -m pytest
          semgrep --config .semgrep .
          vulture src
          interrogate src
""",
    )

    violations = delegation.validate(tmp_path)
    tools = {violation.tool for violation in violations}

    assert {"pytest", "ruff", "basedpyright", "pyright", "semgrep", "vulture", "interrogate"} <= tools


def test_scanner_allows_dev_delegation_and_commented_lines(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        ".pre-commit-config.yaml",
        """
repos:
  - repo: local
    hooks:
      - id: dev-check
        entry: dev check # pytest would be raw here if active
      # - entry: ruff check .
""",
    )
    write_file(
        tmp_path,
        "Makefile",
        "check:\n\tdev check\n\t# python -m pytest\n",
    )
    write_file(
        tmp_path,
        ".github/workflows/ci.yml",
        """
name: ci
jobs:
  quality:
    steps:
      - run: dev check
      - run: "# semgrep --config .semgrep ."
""",
    )

    assert delegation.validate(tmp_path) == []


def test_scanner_allows_python_module_pip_install(tmp_path: Path) -> None:
    write_file(tmp_path, "Makefile", "deps:\n\tpython -m pip install pytest ruff semgrep\n")

    assert delegation.validate(tmp_path) == []


@pytest.mark.parametrize("operator", ["&&", "||", ";", "|"])
def test_scanner_rescans_after_python_module_pip_install_shell_operator(
    tmp_path: Path,
    operator: str,
) -> None:
    write_file(tmp_path, "Makefile", f"deps:\n\tpython -m pip install ruff {operator} ruff check .\n")

    violations = delegation.validate(tmp_path)

    assert [(item.line, item.tool) for item in violations] == [(2, "ruff")]


def test_scanner_blocks_workflow_run_single_line_and_block_forms(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        ".github/workflows/ci.yml",
        """
name: ci
jobs:
  quality:
    steps:
      - run: mypy .
      - run: |
          python -m ruff format --check .
""",
    )

    violations = delegation.validate(tmp_path)

    assert [(item.line, item.tool) for item in violations] == [(6, "mypy"), (8, "ruff")]
