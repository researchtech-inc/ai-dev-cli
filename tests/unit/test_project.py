"""Project config loading tests."""

from pathlib import Path

from ai_dev_cli import _project
from tests.unit._helpers import write_file, write_pyproject


def test_load_config_parses_tool_dev_cli_and_exposes_resolved_sections(monkeypatch, tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """
[tool.dev-cli]
schema_version = 1
cache_dir = ".cache/dev"
tracked_extensions = [".py", ".toml"]
runner = "none"
test_roots = ["custom-tests"]
source_packages = ["src/pkg"]
replace_auto_checks = true

[tool.dev-cli.scopes]
smoke = "custom-tests/smoke"

[tool.dev-cli.lanes.unit]
paths = ["custom-tests/unit"]
timeout = 11

[[tool.dev-cli.checks]]
name = "custom"
description = "Custom check"
run = ["python -m pkg.check"]
cacheable = true
""",
    )
    write_file(tmp_path, "custom-tests/smoke/test_x.py", "def test_x() -> None:\n    assert True\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_project, "_has_tool", lambda name: False)

    cfg = _project.load_config(force_reload=True)

    assert cfg.repo_root == tmp_path
    assert cfg.runs_dir == tmp_path / ".cache" / "dev"
    assert cfg.tracked_extensions == frozenset({".py", ".toml"})
    assert cfg.runner_prefix == ()
    assert cfg.test_roots == ["custom-tests"]
    assert cfg.source_packages == ["src/pkg"]
    assert cfg.scope_aliases["smoke"] == "custom-tests/smoke"
    assert cfg.dev_cli_config["lanes"]["unit"]["paths"] == ["custom-tests/unit"]
    assert cfg.checks[3] == _project.CheckStep(
        name="custom",
        description="Custom check",
        commands=(("python", "-m", "pkg.check"),),
        cacheable=True,
    )


def test_missing_optional_tool_dev_cli_keys_use_bedrock_defaults(monkeypatch, tmp_path: Path) -> None:
    write_pyproject(tmp_path, '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    write_file(tmp_path, "tests/unit/test_x.py", "def test_x() -> None:\n    assert True\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_project, "_has_tool", lambda name: False)

    cfg = _project.load_config(force_reload=True)

    assert cfg.runs_dir == tmp_path / ".tmp" / "dev-runs"
    assert cfg.tracked_extensions == frozenset({".py"})
    assert cfg.test_roots == ["tests"]
    assert cfg.source_packages == []
    assert cfg.hash_files == ("pyproject.toml",)
    assert cfg.step_timeouts == _project.STEP_TIMEOUT_DEFAULTS


def test_bedrock_checks_are_wired_ahead_of_project_checks(monkeypatch, tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """
[tool.dev-cli]
runner = "none"
replace_auto_checks = true

[[tool.dev-cli.checks]]
name = "project-check"
description = "Project check"
run = ["python -m project_check"]
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_project, "_has_tool", lambda name: False)

    cfg = _project.load_config(force_reload=True)
    names = [step.name for step in cfg.checks]

    assert names[:3] == ["bedrock-semgrep", "bedrock-delegation", "bedrock-test-validator"]
    assert names[3] == "project-check"
    assert "bedrock-delegation" in names


def test_default_test_check_uses_configured_unit_lane(monkeypatch, tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """
[tool.dev-cli]
runner = "none"

[tool.dev-cli.lanes.integration]
paths = ["tests/integration"]

[tool.dev-cli.lanes.unit]
paths = ["tests/unit"]
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_project, "_has_tool", lambda name: False)

    cfg = _project.load_config(force_reload=True)
    test_step = next(step for step in cfg.checks if step.name == "test")

    assert test_step.commands == (("dev", "test", "--lane=unit"),)


def test_default_test_check_uses_test_roots_when_no_lanes_are_configured(monkeypatch, tmp_path: Path) -> None:
    write_pyproject(
        tmp_path,
        """
[tool.dev-cli]
runner = "none"
test_roots = ["tests"]
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_project, "_cached_config", None)
    monkeypatch.setattr(_project, "_has_tool", lambda name: False)

    cfg = _project.load_config(force_reload=True)
    test_step = next(step for step in cfg.checks if step.name == "test")

    assert test_step.commands == (("dev", "test"),)
