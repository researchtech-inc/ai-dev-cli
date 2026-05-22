"""Configured probe command support."""

import argparse
import sys
from typing import Any

from ai_dev_cli._project import ProjectConfig, load_config
from ai_dev_cli._runner import run_command


def cmd_probe(args: argparse.Namespace) -> int:
    """Run a configured probe suite."""
    cfg = load_config()
    name = str(args.name)
    section = _probe_section(cfg, name)
    if section is None:
        return _disabled_command(f"probe {name}", f"[tool.dev-cli.probes.{name}]")

    target = section.get("target")
    if not isinstance(target, str) or not target:
        _print_config_error(f"probe '{name}' has no target.", f"set target under [tool.dev-cli.probes.{name}].")
        return 2
    if not (cfg.repo_root / target).exists():
        _print_config_error(
            f"probe target does not exist: {target}",
            f"fix [tool.dev-cli.probes.{name}].target.",
        )
        return 2

    raw_timeout = section.get("timeout")
    timeout_seconds = raw_timeout if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool) else None

    cmd = list(cfg.command("pytest", "--tb=short", "--no-header", "-q", target))
    cmd.extend(args.pytest_args)

    exit_code, summary, _, _ = run_command(
        cmd,
        label=f"probe-{name}",
        log_suffix=f"probe-{name}",
        use_reportlog=True,
        timeout_seconds=timeout_seconds,
    )
    print(summary)
    return exit_code


def _probe_section(cfg: ProjectConfig, name: str) -> dict[str, Any] | None:
    probes = cfg.dev_cli_config.get("probes", {})
    if not isinstance(probes, dict):
        return None
    section = probes.get(name)
    if not isinstance(section, dict) or section.get("enabled") is not True:
        return None
    return section


def _disabled_command(command: str, table: str) -> int:
    print(f"BLOCKED: dev {command} is not enabled for this project. Use {table} instead.", file=sys.stderr)
    return 2


def _print_config_error(message: str, next_step: str) -> None:
    print(f"BLOCKED: {message} Use {next_step.rstrip('.')} instead.", file=sys.stderr)
