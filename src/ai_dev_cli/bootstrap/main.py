"""Dispatch for `dev init` and bootstrap upgrade modes."""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from ai_dev_cli import __version__
from ai_dev_cli._project import find_repo_root
from ai_dev_cli.bootstrap import apply, disposition, profiles, sidecar

AGENT_ONBOARDING = (
    "Codex hook wiring installed. Run `dev info` to inspect hook policy, then use `/hooks` in Codex to trust the "
    "PreToolUse hook for this repository."
)


@dataclass(frozen=True, slots=True)
class _RunContext:
    root: Path
    stdout: TextIO
    stderr: TextIO


def add_init_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `dev init` subcommand to the top-level parser."""
    parser = subparsers.add_parser("init", help="Scaffold or upgrade ai-dev-cli project files")
    _add_init_arguments(parser)
    parser.set_defaults(func=cmd_init)


def run(
    argv: list[str],
    *,
    root: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the bootstrap command directly for tests."""
    parser = argparse.ArgumentParser(prog="dev init")
    _add_init_arguments(parser)
    args = parser.parse_args(argv)
    return _dispatch(args, _RunContext(root or Path.cwd(), stdout or sys.stdout, stderr or sys.stderr))


def cmd_init(args: argparse.Namespace) -> int:
    """Entrypoint used by the top-level dev parser."""
    return _dispatch(args, _RunContext(find_repo_root(Path.cwd()), sys.stdout, sys.stderr))


def _add_init_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", default=None, help="Shipped bootstrap profile")
    parser.add_argument("--profile-file", default=None, help="User-owned profile.toml or profile directory")
    parser.add_argument("--upgrade", action="store_true", help="Run upgrade flow")
    parser.add_argument("--plan", action="store_true", help="Write an upgrade plan")
    parser.add_argument("--apply", action="store_true", help="Apply a fresh upgrade plan")
    parser.add_argument("--recover", metavar="PATH", help="Adopt one user-restored path into management")
    parser.add_argument("--force", action="store_true", help="Allow accepted divergent overwrites")
    parser.add_argument("--accept", action="append", default=[], metavar="PATH", help="Accept one overwrite path")


def _dispatch(args: argparse.Namespace, context: _RunContext) -> int:
    try:
        profile = profiles.load_profile(args.profile, args.profile_file)
        _validate_modes(args)
        accepted = frozenset(sidecar.normalize_path(path) for path in args.accept)
        if args.upgrade:
            result = _dispatch_upgrade(context, args, profile, accepted)
        elif args.plan or args.apply or args.recover:
            result = _blocked(context, "BLOCKED: upgrade flag is missing. Use 'dev init --upgrade' instead.")
        else:
            result = _cmd_first_init(context, profile, force=args.force, accepted_paths=accepted)
    except profiles.ProfileError as exc:
        result = _blocked(context, exc.message)
    except (apply.ApplyError, disposition.DispositionError) as exc:
        result = _blocked(context, exc.message)
    return result


def _dispatch_upgrade(
    context: _RunContext,
    args: argparse.Namespace,
    profile: profiles.Profile,
    accepted_paths: frozenset[str],
) -> int:
    if args.plan:
        return _cmd_plan(context, profile, force=args.force, accepted_paths=accepted_paths)
    if args.apply:
        return _cmd_apply(context, profile, force=args.force, accepted_paths=accepted_paths)
    if args.recover:
        return _cmd_recover(context, args.recover, force=args.force, accepted_paths=accepted_paths)
    return _blocked(context, "BLOCKED: upgrade mode is missing. Use --plan, --apply, or --recover instead.")


def _validate_modes(args: argparse.Namespace) -> None:
    modes = sum(1 for value in (args.plan, args.apply, args.recover is not None) if value)
    if modes > 1:
        raise profiles.ProfileError(
            "BLOCKED: multiple upgrade modes were requested. Use only one of --plan, --apply, or --recover instead."
        )


def _cmd_first_init(
    context: _RunContext,
    profile: profiles.Profile,
    *,
    force: bool,
    accepted_paths: frozenset[str],
) -> int:
    if sidecar.sidecar_path(context.root).exists():
        return _blocked(
            context,
            "BLOCKED: project already managed. Use 'dev init --upgrade' instead.",
        )

    blocked = _blocked_template_targets(context.root, profile, None)
    if not _validate_force_accept(context, blocked, force=force, accepted_paths=accepted_paths):
        return 2
    if blocked and not force:
        for path in blocked:
            context.stderr.write(f"{_block_for_path(context.root, None, path)}\n")
        return 2
    _write_all_templates(context.root, profile)
    managed_files = sidecar.managed_file_hashes(context.root, profiles.template_markers(profile))
    sidecar.write_sidecar(
        context.root,
        sidecar.build_sidecar(profile=profile.name, ai_dev_cli_version=__version__, files=managed_files),
    )
    context.stdout.write("Scaffold completed.\n")
    context.stdout.write(f"{AGENT_ONBOARDING}\n")
    return 0


def _cmd_plan(
    context: _RunContext,
    profile: profiles.Profile,
    *,
    force: bool,
    accepted_paths: frozenset[str],
) -> int:
    managed = sidecar.read_sidecar(context.root)
    plan = disposition.build_plan(context.root, profile, managed)
    plan_path = _write_plan(context.root, plan.rows)
    context.stdout.write(f"Plan: {plan_path.relative_to(context.root).as_posix()}\n")
    context.stdout.write(f"Blocks: {len(plan.blocks)}\n")
    if not _validate_force_accept(context, plan.blocks, force=force, accepted_paths=accepted_paths):
        return 2
    if plan.blocks and not force:
        for path in plan.blocks:
            context.stderr.write(f"{_block_for_path(context.root, managed, path)}\n")
        return 2
    return 0


def _cmd_apply(
    context: _RunContext,
    profile: profiles.Profile,
    *,
    force: bool,
    accepted_paths: frozenset[str],
) -> int:
    managed = sidecar.read_sidecar(context.root)
    if managed is None:
        return _blocked(context, "BLOCKED: project is not managed. Use 'dev init' instead.")
    plan = disposition.build_plan(context.root, profile, managed)
    plan_path = _write_plan(context.root, plan.rows)
    context.stdout.write(f"Plan: {plan_path.relative_to(context.root).as_posix()}\n")
    if not _validate_force_accept(context, plan.blocks, force=force, accepted_paths=accepted_paths):
        return 2
    if plan.blocks and not force:
        for path in plan.blocks:
            context.stderr.write(f"{_block_for_path(context.root, managed, path)}\n")
        return 2
    rows = disposition.parse_csv(plan_path)
    updated = apply.execute_plan(context.root, rows, profile, managed, accepted_paths=accepted_paths)
    sidecar.write_sidecar(context.root, updated)
    context.stdout.write("Applied upgrade plan.\n")
    return 0


def _cmd_recover(
    context: _RunContext,
    path: str,
    *,
    force: bool,
    accepted_paths: frozenset[str],
) -> int:
    managed = sidecar.read_sidecar(context.root)
    normalized = sidecar.normalize_path(path)
    if managed is None:
        return _blocked(
            context,
            f"BLOCKED: path '{normalized}' is not managed by ai-dev-cli. Use 'dev init' instead.",
        )
    blocked = (normalized,) if (context.root / normalized).exists() else ()
    if not _validate_force_accept(context, blocked, force=force, accepted_paths=accepted_paths):
        return 2
    updated = apply.recover_path(context.root, normalized, managed)
    sidecar.write_sidecar(context.root, updated)
    context.stdout.write(f"Recovered {normalized}.\n")
    return 0


def _write_all_templates(root: Path, profile: profiles.Profile) -> None:
    for template in profile.templates:
        target = root / template.target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(profiles.render_template(profile, template, project_name=root.name))


def _write_plan(root: Path, rows: tuple[disposition.DispositionRow, ...]) -> Path:
    plan_path = root / ".tmp" / "dev-runs" / "dev-init-upgrade-plan.csv"
    disposition.write_csv(plan_path, rows)
    return plan_path


def _blocked_template_targets(
    root: Path,
    profile: profiles.Profile,
    managed: sidecar.Sidecar | None,
) -> tuple[str, ...]:
    blocked = []
    for target in profiles.expected_targets(profile):
        state = sidecar.classify_path(root, managed, target)
        if state.state in {"unmanaged", "managed-divergent"}:
            blocked.append(target)
    return tuple(blocked)


def _validate_force_accept(
    context: _RunContext,
    blocked_paths: tuple[str, ...],
    *,
    force: bool,
    accepted_paths: frozenset[str],
) -> bool:
    blocked = frozenset(sidecar.normalize_path(path) for path in blocked_paths)
    if accepted_paths and not force:
        context.stderr.write("BLOCKED: --accept requires --force. Use --force --accept <path> instead.\n")
        return False
    if force and not accepted_paths:
        context.stderr.write("BLOCKED: --force requires --accept <path>. Use --force --accept <path> instead.\n")
        return False
    missing = blocked - accepted_paths
    if force and missing:
        path = sorted(missing)[0]
        context.stderr.write(f"BLOCKED: {path} was not accepted. Use --force --accept {path} instead.\n")
        return False
    extra = accepted_paths - blocked
    if extra:
        path = sorted(extra)[0]
        context.stderr.write(f"BLOCKED: {path} is not a blocked target. Use only accepted blocked paths instead.\n")
        return False
    return True


def _block_for_path(root: Path, managed: sidecar.Sidecar | None, path: str) -> str:
    state = sidecar.classify_path(root, managed, path)
    if state.state == "managed-divergent":
        return (
            f"BLOCKED: {path} was edited outside ai-dev-cli management. "
            f"Use --force --accept {path} after reviewing git diff instead."
        )
    if state.state == "managed-clean":
        return (
            f"BLOCKED: {path} already exists and would be overwritten. "
            f"Use --force --accept {path} after reviewing git diff instead."
        )
    return (
        f"BLOCKED: {path} is unmanaged and would be overwritten. "
        f"Use --force --accept {path} after backing up the file instead."
    )


def _blocked(context: _RunContext, message: str) -> int:
    context.stderr.write(f"{message}\n")
    return 2
