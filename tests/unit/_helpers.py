"""Shared unit-test helpers for ai-dev-cli."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any


def write_pyproject(root: Path, body: str = "") -> Path:
    path = root / "pyproject.toml"
    path.write_text(body, encoding="utf-8")
    return path


def write_file(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_custom_profile(profile_root: Path) -> None:
    (profile_root / "profile.toml").write_text(
        """name = "custom"
version = 1

[tool.dev-cli]
schema_version = 1
test_roots = ["tests"]

[[templates]]
source = "template.txt"
target = "generated.txt"
merge = "replace-managed"
""",
        encoding="utf-8",
    )
    (profile_root / "template.txt").write_text("project = {{ project_name }}\n", encoding="utf-8")


def project_config(root: Path, **overrides: Any) -> SimpleNamespace:
    runs_dir = root / ".tmp" / "dev-runs"
    values: dict[str, Any] = {
        "repo_root": root,
        "runs_dir": runs_dir,
        "state_file": runs_dir / ".state.json",
        "dev_cli_config": {},
        "source_to_test": {},
        "scope_aliases": {},
        "markers": {},
        "test_groups": {},
        "checks": (),
        "tracked_extensions": frozenset({".py"}),
        "runner_prefix": (),
        "test_roots": ["tests"],
        "infrastructure": (),
        "source_packages": ["src/pkg"],
        "coverage_sources": [],
        "coverage_fail_under": None,
        "hash_globs": (),
        "hash_files": (),
        "hash_optional_files": (),
        "step_timeouts": {},
        "config_hash": "config-a",
        "has_ruff": False,
        "has_basedpyright": False,
        "has_pyright": False,
        "has_mypy": False,
        "has_vulture": False,
        "has_interrogate": False,
        "has_semgrep": False,
        "has_make": False,
        "command": lambda executable, *args: (executable, *args),
    }
    values.update(overrides)
    return SimpleNamespace(**values)
