"""Dev CLI entrypoint for test, lint, check, and configured workflows."""

import argparse
import json
import sys
import time
from typing import Any

from ai_dev_cli._infra import detect, format_status
from ai_dev_cli._lane import LaneConfig, lane_from_path, load_lanes
from ai_dev_cli._project import CheckStep, ProjectConfig, load_config
from ai_dev_cli._rerun import compute_rerun_set
from ai_dev_cli._runner import print_skip, run_check_step, run_command
from ai_dev_cli._scope import detect_test_scope, get_source_dirs_for_test_dirs, resolve_scope
from ai_dev_cli._state import check_idempotency, hash_tracked_files, save_run_state
from ai_dev_cli.bootstrap.main import add_init_parser
from ai_dev_cli.hook.config import HookConfigError, load_hook_config
from ai_dev_cli.probe import cmd_probe

LANE_PRIORITY = ("qualification", "integration", "unit")
EXAMPLE_MODES = ("static", "smoke", "live")
BEDROCK_CHECK_STEP_NAMES = frozenset({
    "bedrock-semgrep",
    "bedrock-delegation",
    "bedrock-test-validator",
})


def _relative_runs_dir() -> str:
    cfg = load_config()
    return f"{cfg.runs_dir.relative_to(cfg.repo_root).as_posix()}/"


def _config_section(cfg: ProjectConfig, key: str) -> dict[str, Any]:
    raw = cfg.dev_cli_config.get(key, {})
    return raw if isinstance(raw, dict) else {}


def _enabled_section(cfg: ProjectConfig, key: str) -> dict[str, Any] | None:
    section = _config_section(cfg, key)
    return section if section.get("enabled") is True else None


def _disabled_command(command: str, table: str) -> int:
    print(f"BLOCKED: dev {command} is not enabled for this project. Use {table} instead.", file=sys.stderr)
    return 2


def _print_config_error(message: str, next_step: str) -> None:
    print(f"BLOCKED: {message} Use {next_step.rstrip('.')} instead.", file=sys.stderr)


def _lane_for_scope(scope: str, scope_dirs: list[str]) -> str | None:
    lanes = {lane for path in scope_dirs if (lane := lane_from_path(path)) is not None}
    if not lanes:
        return None
    if len(lanes) > 1:
        print(
            f"BLOCKED: scope '{scope}' spans multiple lanes ({', '.join(sorted(lanes))}). "
            "Use dev test --lane=<lane> instead.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return next(iter(lanes))


def _lane_for_auto_scope(scope_dirs: list[str]) -> str | None:
    lanes = {lane for path in scope_dirs if (lane := lane_from_path(path)) is not None}
    for lane in LANE_PRIORITY:
        if lane in lanes:
            return lane
    return sorted(lanes)[0] if lanes else None


def _default_test_selection(cfg: ProjectConfig, lanes: dict[str, LaneConfig]) -> tuple[str | None, str, list[str]]:
    if "unit" in lanes:
        return "unit", "lane-unit", list(lanes["unit"].paths)
    if lanes:
        lane = sorted(lanes)[0]
        return lane, f"lane-{lane}", list(lanes[lane].paths)
    return None, "test-roots", list(cfg.test_roots)


def _build_test_selection(args: argparse.Namespace) -> tuple[str | None, str, list[str]]:
    cfg = load_config()
    lanes = load_lanes()

    if args.scope:
        scope_dirs = resolve_scope(args.scope)
        lane = _lane_for_scope(args.scope, scope_dirs)
        return lane, args.scope, scope_dirs

    if args.lane:
        lane_config = lanes.get(args.lane)
        if lane_config is None:
            lane_names = ", ".join(sorted(lanes)) or "(none configured)"
            print(
                f"BLOCKED: unknown test lane '{args.lane}'. Configured lanes: {lane_names}. Use dev info instead.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        return args.lane, f"lane-{args.lane}", list(lane_config.paths)

    scope_dirs = resolve_scope(None)
    if not scope_dirs:
        return _default_test_selection(cfg, lanes)

    return _lane_for_auto_scope(scope_dirs), "auto", scope_dirs


def _is_collection_failure_summary(summary: str) -> bool:
    return "collection failed" in summary


def cmd_test(args: argparse.Namespace) -> int:
    """Run tests by scope or by full lane."""
    cfg = load_config()
    lane, scope_label, scope_dirs = _build_test_selection(args)
    lane_config = load_lanes().get(lane) if lane is not None else None

    if lane is not None and lane_config is None:
        print(f"BLOCKED: lane '{lane}' is not configured. Use [tool.dev-cli.lanes] instead.", file=sys.stderr)
        return 2

    cmd = list(cfg.command("pytest"))
    pytest_targets: list[str]
    if args.rerun_failed:
        rerun = compute_rerun_set(cfg.repo_root, tuple(scope_dirs))
        if not rerun.should_run:
            return 0
        cmd.extend(["-p", "no:testmon"])
        pytest_targets = list(rerun.nodeids)
        scope_label = f"{scope_label}-rerun-failed"
    else:
        if lane_config is not None and lane_config.testmon:
            cmd.append("--testmon")
        pytest_targets = scope_dirs

    xdist_args = lane_config.xdist if lane_config is not None else ()
    cmd.extend(xdist_args)
    cmd.extend(["--tb=short", "--no-header", "-q"])
    cmd.extend(pytest_targets)

    worker_label = " ".join(xdist_args) or "no-xdist"
    lane_label = lane or "unlaned"
    command_key = f"test:{lane_label}:{scope_label}:{worker_label}"
    hash_dirs = [*scope_dirs, *cfg.source_packages]
    hash_dirs.extend(get_source_dirs_for_test_dirs(scope_dirs))
    file_hash = hash_tracked_files(*hash_dirs)

    if not args.rerun_failed:
        prev = check_idempotency(command_key, file_hash)
        if prev and not _is_collection_failure_summary(prev.summary):
            return print_skip(f"test {scope_label}", prev.timestamp, prev.exit_code, prev.log_file)

    exit_code, summary, log_file, is_collection_failure = run_command(
        cmd,
        f"test {scope_label}",
        log_suffix=f"test-{scope_label}",
        use_reportlog=True,
        slow_threshold=lane_config.slow_threshold if lane_config is not None else None,
    )
    if not is_collection_failure:
        save_run_state(command_key, " ".join(cmd), exit_code, summary, log_file, file_hash)
    print(summary)
    return exit_code


def cmd_lint(args: argparse.Namespace) -> int:
    """Run ruff check and format check."""
    cfg = load_config()

    if not cfg.has_ruff:
        print("SKIP  lint - ruff not found")
        return 0

    if args.fix:
        return cmd_format(args)

    exit_code, summary, log_file, _ = run_command(
        cfg.command("ruff", "check", "."),
        "lint:check",
        log_suffix="lint-check",
    )
    if exit_code != 0:
        print(summary)
        print("\n  Auto-fix: dev format")
        return exit_code

    exit_code2, summary2, log_file2, _ = run_command(
        cfg.command("ruff", "format", "--check", "."),
        "lint:format",
        log_suffix="lint-format",
    )
    if exit_code2 != 0:
        print(summary2)
        print("\n  Auto-fix: dev format")
        return exit_code2

    print("PASS  lint")
    print(f"  Details: {log_file}, {log_file2}")
    return 0


def cmd_format(args: argparse.Namespace) -> int:
    """Auto-fix ruff formatting and lint issues."""
    cfg = load_config()

    if not cfg.has_ruff:
        print("SKIP  format - ruff not found")
        return 0

    if getattr(args, "check", False):
        exit_code, summary, log_file, _ = run_command(
            cfg.command("ruff", "format", "--check", "."),
            "format:check",
            log_suffix="format-check",
        )
        if exit_code != 0:
            print(summary)
            return exit_code

        print("PASS  format - formatting is clean")
        print(f"  Details: {log_file}")
        return 0

    exit_code1, summary1, log_file1, _ = run_command(
        cfg.command("ruff", "format", "."),
        "format:ruff-format",
        log_suffix="format-fmt",
    )
    exit_code2, summary2, log_file2, _ = run_command(
        cfg.command("ruff", "check", "--fix", "."),
        "format:ruff-fix",
        log_suffix="format-fix",
    )

    if exit_code1 != 0 or exit_code2 != 0:
        print(summary1 if exit_code1 != 0 else summary2)
        return exit_code1 or exit_code2

    print("PASS  format - all files formatted and auto-fixed")
    print(f"  Details: {log_file1}, {log_file2}")
    return 0


def cmd_typecheck(_args: argparse.Namespace) -> int:
    """Run the configured type checker."""
    cfg = load_config()
    log_files: list[str] = []

    if cfg.has_basedpyright:
        exit_code = _run_typecheck_command(
            cfg.command("basedpyright", "--level", "warning"),
            "typecheck:source",
            "typecheck-src",
            log_files,
        )
        if exit_code != 0:
            return exit_code

        if (cfg.repo_root / "pyrightconfig.tests.json").exists():
            exit_code = _run_typecheck_command(
                cfg.command("basedpyright", "--level", "error", "-p", "pyrightconfig.tests.json"),
                "typecheck:tests",
                "typecheck-tests",
                log_files,
            )
            if exit_code != 0:
                return exit_code
    elif cfg.has_pyright:
        exit_code = _run_typecheck_command(cfg.command("pyright"), "typecheck", "typecheck", log_files)
        if exit_code != 0:
            return exit_code
    elif cfg.has_mypy:
        exit_code = _run_typecheck_command(cfg.command("mypy", "."), "typecheck", "typecheck", log_files)
        if exit_code != 0:
            return exit_code
    else:
        print("SKIP  typecheck - no type checker found (basedpyright, pyright, mypy)")
        return 0

    print("PASS  typecheck")
    print(f"  Details: {', '.join(log_files)}")
    return 0


def _run_typecheck_command(command: tuple[str, ...], label: str, log_suffix: str, log_files: list[str]) -> int:
    exit_code, summary, log_file, _ = run_command(command, label, log_suffix=log_suffix)
    log_files.append(log_file)
    if exit_code != 0:
        print(summary)
    return exit_code


def cmd_check(args: argparse.Namespace) -> int:
    """Run checks in order."""
    cfg = load_config()
    steps = _selected_check_steps(cfg, getattr(args, "steps", None))
    if steps is None:
        return 2

    check_start = time.monotonic()

    for step in steps:
        exit_code = run_check_step(step.name, step.commands, cacheable=step.cacheable)
        if exit_code != 0:
            return exit_code

    total = time.monotonic() - check_start
    print(f"\nPASS  check - all checks passed ({total:.1f}s)")
    print(f"  Details: {_relative_runs_dir()}")
    return 0


def _selected_check_steps(cfg: ProjectConfig, raw_steps: str | None) -> tuple[CheckStep, ...] | None:
    if not raw_steps:
        return cfg.checks

    requested = tuple(dict.fromkeys(step.strip() for step in raw_steps.split(",") if step.strip()))
    by_name = {step.name: step for step in cfg.checks}
    missing = tuple(step for step in requested if step not in by_name)
    if missing:
        print(f"BLOCKED: unknown check step '{missing[0]}'. Use dev info instead.", file=sys.stderr)
        return None

    bedrock_steps = tuple(step for step in cfg.checks if step.name in BEDROCK_CHECK_STEP_NAMES)
    additive_steps = tuple(step for step in cfg.checks if step.name not in BEDROCK_CHECK_STEP_NAMES)
    return (*bedrock_steps, *(step for step in additive_steps if step.name in requested))


def cmd_verify(_args: argparse.Namespace) -> int:
    """Run the configured validation sequence."""
    cfg = load_config()
    section = _enabled_section(cfg, "verify")
    if section is None:
        return _disabled_command("verify", "[tool.dev-cli.verify]")

    steps = _verify_steps(cfg, section)
    if steps is None:
        return 2

    for label, command in steps:
        exit_code, summary, _, _ = run_command(command, label, log_suffix=label)
        print(summary)
        if exit_code != 0:
            return exit_code

    print("PASS  verify")
    print(f"  Details: {_relative_runs_dir()}")
    return 0


def _verify_steps(
    cfg: ProjectConfig,
    section: dict[str, Any],
) -> tuple[tuple[str, tuple[str, ...]], ...] | None:
    raw_steps = section.get("steps", [])
    if not isinstance(raw_steps, list | tuple) or not raw_steps:
        _print_config_error("dev verify has no configured steps.", "add steps under [tool.dev-cli.verify].")
        return None

    steps: list[tuple[str, tuple[str, ...]]] = []
    for index, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict):
            _print_config_error("dev verify step entries must be tables.", "use { command = 'check' } style steps.")
            return None
        parsed = _verify_step_command(cfg, raw, index)
        if parsed is None:
            return None
        steps.append(parsed)
    return tuple(steps)


def _verify_step_command(
    cfg: ProjectConfig,
    entry: dict[str, Any],
    index: int,
) -> tuple[str, tuple[str, ...]] | None:
    command = entry.get("command")
    if command == "check":
        return f"verify-{index}-check", cfg.command("dev", "check")
    if command == "test":
        lane = entry.get("lane")
        if lane is not None and not isinstance(lane, str):
            _print_config_error("dev verify test step has an invalid lane.", "use lane = '<configured-lane>'.")
            return None
        args = ("test", f"--lane={lane}") if lane else ("test",)
        label = f"verify-{index}-test{f'-{lane}' if lane else ''}"
        return label, cfg.command("dev", *args)
    if command == "examples":
        mode = entry.get("mode", "smoke")
        if mode not in EXAMPLE_MODES:
            _print_config_error("dev verify examples step has an invalid mode.", "use static, smoke, or live.")
            return None
        return f"verify-{index}-examples-{mode}", cfg.command("dev", "examples", f"--{mode}")

    _print_config_error("dev verify step has an unsupported command.", "use check, test, or examples.")
    return None


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run a configured benchmark target."""
    cfg = load_config()
    section = _enabled_section(cfg, "benchmark")
    if section is None:
        return _disabled_command("benchmark", "[tool.dev-cli.benchmark]")

    target = section.get("target")
    if not isinstance(target, str) or not target:
        _print_config_error("dev benchmark has no target.", "set target under [tool.dev-cli.benchmark].")
        return 2

    env = _env_from_config(section.get("run_env"))
    tier_env = section.get("tier_env")
    cases_env = section.get("cases_env")
    action = "benchmark"

    if args.smoke:
        smoke_tier = section.get("smoke_tier")
        if isinstance(tier_env, str) and isinstance(smoke_tier, str):
            env[tier_env] = smoke_tier
        action = "benchmark-smoke"
    elif args.cases:
        if not isinstance(cases_env, str):
            _print_config_error("dev benchmark --cases has no cases_env.", "set cases_env in benchmark config.")
            return 2
        env[cases_env] = args.cases
        action = "benchmark-cases"
    else:
        if not isinstance(tier_env, str):
            _print_config_error("dev benchmark --tier has no tier_env.", "set tier_env in benchmark config.")
            return 2
        env[tier_env] = args.tier
        action = f"benchmark-{args.tier}"

    cmd = list(cfg.command("pytest", "-q", "--tb=short", "--no-header", target))
    exit_code, summary, _, _ = run_command(
        cmd,
        action,
        log_suffix=action,
        use_reportlog=True,
        env=env,
        slow_threshold=_slow_threshold_for_lane(section.get("path_lane")),
    )
    print(summary)
    return exit_code


def _env_from_config(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def cmd_examples(args: argparse.Namespace) -> int:
    """Run configured examples validation."""
    cfg = load_config()
    section = _enabled_section(cfg, "examples")
    if section is None:
        return _disabled_command("examples", "[tool.dev-cli.examples]")

    tier = _example_tier(args)
    target = section.get(f"{tier}_path")
    if not isinstance(target, str) or not target:
        _print_config_error(f"dev examples --{tier} has no configured path.", "set the path in examples config.")
        return 2
    if not (cfg.repo_root / target).exists():
        _print_config_error(f"configured examples path does not exist: {target}", "fix [tool.dev-cli.examples].")
        return 2

    xdist = _example_xdist(section)
    cmd = list(cfg.command("pytest", *xdist, "--tb=short", "--no-header", "-q", target))
    exit_code, summary, _, _ = run_command(
        cmd,
        f"examples-{tier}",
        log_suffix=f"examples-{tier}",
        use_reportlog=True,
        slow_threshold=_slow_threshold_for_lane(section.get("path_lane")),
    )
    print(summary)
    return exit_code


def _example_tier(args: argparse.Namespace) -> str:
    if args.live:
        return "live"
    if args.smoke:
        return "smoke"
    return "static"


def _example_xdist(section: dict[str, Any]) -> tuple[str, ...]:
    path_lane = section.get("path_lane")
    if not isinstance(path_lane, str):
        return ()
    lane = load_lanes().get(path_lane)
    return lane.xdist if lane is not None else ()


def _slow_threshold_for_lane(raw_lane: Any) -> float | None:
    if not isinstance(raw_lane, str):
        return None
    lane = load_lanes().get(raw_lane)
    return lane.slow_threshold if lane is not None else None


def cmd_status(_args: argparse.Namespace) -> int:
    """Show changed files, affected scopes, and recent run state."""
    cfg = load_config()
    test_dirs, source_dirs = detect_test_scope()

    print("Changed source modules:")
    if source_dirs:
        for directory in source_dirs:
            print(f"  {directory}")
    else:
        print("  (none)")

    print("\nAffected test directories:")
    if test_dirs:
        for directory in test_dirs:
            print(f"  {directory}")
    else:
        print("  (none detected)")

    if cfg.state_file.exists():
        try:
            data = json.loads(cfg.state_file.read_text())
        except json.JSONDecodeError:
            data = {}
        runs = data.get("runs", {}) if isinstance(data, dict) else {}
        if runs:
            print("\nLast runs:")
            for key, run in runs.items():
                if isinstance(run, dict):
                    status = "PASS" if run.get("exit_code") == 0 else "FAIL"
                    print(f"  {key}: {status} at {str(run.get('timestamp', ''))[:19]}")

    print("\nSuggested next command:")
    print(f"  {_suggest_next_command(cfg, test_dirs)}")
    return 0


def _suggest_next_command(cfg: ProjectConfig, test_dirs: list[str]) -> str:
    if not test_dirs:
        lanes = load_lanes()
        return "dev test --lane=unit" if "unit" in lanes else "dev test"

    if len(test_dirs) == 1:
        return f"dev test {_scope_alias_for_path(cfg, test_dirs[0])}"

    lane = _lane_for_auto_scope(test_dirs)
    return f"dev test --lane={lane}" if lane is not None else "dev test"


def _scope_alias_for_path(cfg: ProjectConfig, path: str) -> str:
    for alias, target in cfg.scope_aliases.items():
        if target == path:
            return alias
    return path


def cmd_info(_args: argparse.Namespace) -> int:
    """Print usage, lanes, checks, scopes, and infrastructure status."""
    cfg = load_config()
    lanes = load_lanes()

    print("dev - Development CLI for test/lint/check workflows.")
    print()
    print("Commands:")
    print(f"  dev test [SCOPE] [--lane {_choice_text(lanes)}] [--rerun-failed]")
    print("  dev format [--check]")
    print("  dev lint")
    print("  dev typecheck")
    print("  dev check [--steps STEP[,STEP...]]")
    print("  dev verify")
    print("  dev examples (--static | --smoke | --live)")
    print(f"  dev benchmark (--smoke | --cases GLOB | --tier {_choice_text(_benchmark_tiers(cfg))})")
    print("  dev probe NAME [pytest_args...]")
    print("  dev status")
    print("  dev info")
    print("  dev init")
    print()

    _print_log_info()
    print()

    print("Test lanes:")
    if lanes:
        for lane in lanes.values():
            print(f"  {lane.name}: {', '.join(lane.paths)}")
    else:
        print("  (none configured)")
    print()

    print("Configured workflows:")
    print(f"  verify: {_enabled_label(cfg, 'verify')}")
    print(f"  examples: {_enabled_label(cfg, 'examples')}")
    print(f"  benchmark: {_enabled_label(cfg, 'benchmark')}")
    print()

    _print_probe_info(cfg)
    print()

    _print_hook_info()
    print()

    print("Check pipeline (dev check):")
    for index, step in enumerate(cfg.checks, 1):
        commands = " && ".join(" ".join(command) for command in step.commands)
        print(f"  {index}. {step.name}: {step.description}")
        print(f"     {commands}")
    print()

    scopes = ", ".join(sorted(cfg.scope_aliases)) or "(none configured)"
    print(f"Available test scopes: {scopes}")
    print(f"Runner: {' '.join(cfg.runner_prefix) or '(bare)'}")

    if cfg.infrastructure:
        print()
        statuses = detect(cfg.infrastructure, cfg.repo_root)
        print(format_status(statuses))

    return 0


def _print_hook_info() -> None:
    print("Hook:")
    try:
        hook_config = load_hook_config()
    except HookConfigError as exc:
        print(f"  config error: {exc.message}")
        return

    disabled = ", ".join(sorted(hook_config.disabled_builtin_rule_ids)) or "(none)"
    print("  entrypoint: python -m ai_dev_cli.hook")
    print(f"  disabled built-ins: {disabled}")
    print(f"  project rules: {len(hook_config.project_rules)}")


def _print_log_info() -> None:
    print("Logs:")
    print("  Details: .tmp/dev-runs/<YYYYMMDD-HHMMSS>-<label>.log")
    print("  Pytest runs also write matching .tmp/dev-runs/<YYYYMMDD-HHMMSS>-<label>.jsonl report logs.")
    print("  .tmp/dev-runs/.state.json stores idempotency and step-cache metadata.")


def _print_probe_info(cfg: ProjectConfig) -> None:
    print("Configured probes:")
    probes = _enabled_probes(cfg)
    if not probes:
        print("  (none enabled)")
        return
    for name, target in probes.items():
        print(f"  {name}: {target}")


def _choice_text(values: dict[str, Any] | tuple[str, ...]) -> str:
    choices = tuple(values) if isinstance(values, dict) else values
    return "{" + ",".join(sorted(choices)) + "}" if choices else "(none configured)"


def _benchmark_tiers(cfg: ProjectConfig) -> tuple[str, ...]:
    raw = _config_section(cfg, "benchmark").get("tiers", ())
    return tuple(item for item in raw if isinstance(item, str)) if isinstance(raw, list | tuple) else ()


def _enabled_label(cfg: ProjectConfig, key: str) -> str:
    return "enabled" if _enabled_section(cfg, key) is not None else "disabled"


def _enabled_probes(cfg: ProjectConfig) -> dict[str, str]:
    raw = cfg.dev_cli_config.get("probes", {})
    if not isinstance(raw, dict):
        return {}
    probes: dict[str, str] = {}
    for name, section in raw.items():
        if not isinstance(name, str) or not isinstance(section, dict) or section.get("enabled") is not True:
            continue
        target = section.get("target")
        if isinstance(target, str):
            probes[name] = target
    return probes


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level dev CLI argument parser."""
    cfg = load_config()
    scope_names = ", ".join(sorted(cfg.scope_aliases)) or "configured scope"

    parser = argparse.ArgumentParser(
        prog="dev",
        description="Development CLI - enforces correct test/lint/check workflows.",
    )
    sub = parser.add_subparsers(dest="command")

    p_test = sub.add_parser("test", help="Run tests by scope or lane")
    test_target = p_test.add_mutually_exclusive_group()
    test_target.add_argument("scope", nargs="?", help=f"Test scope: {scope_names}")
    _add_lane_argument(test_target)
    p_test.add_argument("--rerun-failed", action="store_true", help="Rerun filtered last-failed tests")
    p_test.set_defaults(func=cmd_test)

    p_lint = sub.add_parser("lint", help="Check lint")
    p_lint.add_argument("--fix", action="store_true", help="Auto-fix (same as 'dev format')")
    p_lint.set_defaults(func=cmd_lint)

    p_format = sub.add_parser("format", help="Auto-fix formatting and lint")
    p_format.add_argument("--check", action="store_true", help="Check formatting without writing")
    p_format.set_defaults(func=cmd_format)

    p_typecheck = sub.add_parser("typecheck", help="Run type checker")
    p_typecheck.set_defaults(func=cmd_typecheck)

    p_check = sub.add_parser("check", help="Run checks")
    p_check.add_argument("--steps", help="Comma-separated check step names to run")
    p_check.set_defaults(func=cmd_check)

    p_verify = sub.add_parser("verify", help="Run configured validation")
    p_verify.set_defaults(func=cmd_verify)

    p_benchmark = sub.add_parser("benchmark", help="Run configured benchmark tiers")
    benchmark_group = p_benchmark.add_mutually_exclusive_group(required=True)
    benchmark_group.add_argument("--smoke", action="store_true", help="Run representative benchmark cells")
    benchmark_group.add_argument("--cases", metavar="GLOB", help="Run benchmark cases matching a glob")
    _add_benchmark_tier_argument(cfg, benchmark_group)
    p_benchmark.set_defaults(func=cmd_benchmark)

    p_examples = sub.add_parser("examples", help="Run configured examples validation")
    examples_group = p_examples.add_mutually_exclusive_group(required=True)
    examples_group.add_argument("--static", action="store_true", help="Run static examples checks")
    examples_group.add_argument("--smoke", action="store_true", help="Run smoke examples checks")
    examples_group.add_argument("--live", action="store_true", help="Run live examples checks")
    p_examples.set_defaults(func=cmd_examples)

    p_probe = sub.add_parser("probe", help="Run a configured probe suite")
    p_probe.add_argument("name", help="Probe name from [tool.dev-cli.probes.<name>]")
    p_probe.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Arguments forwarded verbatim to pytest")
    p_probe.set_defaults(func=cmd_probe)

    p_status = sub.add_parser("status", help="Show changed files, last runs, suggestions")
    p_status.set_defaults(func=cmd_status)

    p_info = sub.add_parser("info", help="Show detailed usage and project state")
    p_info.set_defaults(func=cmd_info)

    add_init_parser(sub)

    return parser


def _add_lane_argument(group: Any) -> None:
    lane_names = tuple(sorted(load_lanes()))
    kwargs: dict[str, Any] = {"default": None, "help": "Run a configured full lane"}
    if lane_names:
        kwargs["choices"] = lane_names
    group.add_argument("--lane", **kwargs)


def _add_benchmark_tier_argument(
    cfg: ProjectConfig,
    group: Any,
) -> None:
    kwargs: dict[str, Any] = {"metavar": "TIER", "help": "Run a configured benchmark tier"}
    tiers = _benchmark_tiers(cfg)
    if tiers:
        kwargs["choices"] = tiers
    group.add_argument("--tier", **kwargs)


def main() -> None:
    """Parse command-line arguments and dispatch the selected command."""
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        exit_code = cmd_info(argparse.Namespace())
    else:
        exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
