"""Test scope detection from git changes and source-to-test mapping."""

import sys
from pathlib import PurePosixPath
from typing import Never

from ai_dev_cli._lane import LaneConfig, lane_from_path, load_lanes
from ai_dev_cli._project import ProjectConfig, load_config
from ai_dev_cli._state import git_changed_files


def detect_test_scope() -> tuple[list[str], list[str]]:
    """Detect which test directories should run from changed files."""
    cfg = load_config()
    changed = git_changed_files()
    if not changed:
        return [], []

    test_dirs: set[str] = set()
    source_dirs: set[str] = set()

    for filepath in changed:
        for test_root in cfg.test_roots:
            if filepath.startswith(f"{test_root}/"):
                test_dir = _changed_test_dir(filepath, test_root, cfg)
                if test_dir:
                    test_dirs.add(test_dir)

        for source_prefix, mapped_test_dirs in cfg.source_to_test.items():
            if filepath == source_prefix or filepath.startswith(f"{source_prefix}/"):
                source_dirs.add(source_prefix)
                test_dirs.update(mapped_test_dirs)

        if filepath.endswith("conftest.py"):
            return list(cfg.test_roots), sorted(source_dirs)

    return sorted(test_dirs), sorted(source_dirs)


def resolve_scope(scope: str | None) -> list[str]:
    """Resolve a scope argument to test directories."""
    cfg = load_config()

    if scope is None:
        dirs, _ = detect_test_scope()
        _enforce_blast_radius(dirs, cfg)
        return dirs

    if scope in cfg.scope_aliases:
        return [cfg.scope_aliases[scope]]

    if scope.endswith(".py"):
        suggestion = _suggest_scope_for_path(scope, cfg)
        hint = f"dev test {suggestion}" if suggestion else "a configured scope name"
        _exit_scope_error(
            "'dev test' takes a scope name, not a file path.",
            hint,
            cfg,
        )

    if (cfg.repo_root / scope).exists():
        return [scope]

    for test_root in cfg.test_roots:
        test_path = f"{test_root}/{scope}"
        if (cfg.repo_root / test_path).exists():
            return [test_path]

    _exit_scope_error(f"Unknown test scope '{scope}'.", "dev status to inspect affected scopes", cfg)


def get_source_dirs_for_test_dirs(test_dirs: list[str]) -> list[str]:
    """Reverse lookup source dirs for selected test dirs."""
    cfg = load_config()
    scope_set = set(test_dirs)
    if any(root in scope_set for root in cfg.test_roots):
        return sorted(cfg.source_to_test.keys())

    source_dirs: set[str] = set()
    for src, tests in cfg.source_to_test.items():
        if scope_set.intersection(tests):
            source_dirs.add(src)
    return sorted(source_dirs)


def _exit_scope_error(message: str, next_step: str, cfg: ProjectConfig) -> Never:
    scopes = ", ".join(sorted(cfg.scope_aliases)) or "(none configured)"
    print(
        f"BLOCKED: {message} Available scopes: {scopes}. Use {next_step} instead.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def _suggest_scope_for_path(file_path: str, cfg: ProjectConfig) -> str | None:
    parts = PurePosixPath(file_path).parts
    for test_root in cfg.test_roots:
        root_parts = PurePosixPath(test_root).parts
        if parts[: len(root_parts)] == root_parts and len(parts) > len(root_parts):
            candidate = parts[len(root_parts)]
            if candidate in cfg.scope_aliases:
                return candidate
    return None


def _changed_test_dir(filepath: str, test_root: str, cfg: ProjectConfig) -> str | None:
    lane_dir = _changed_lane_test_dir(filepath, cfg)
    if lane_dir is not None:
        return lane_dir

    parts = filepath.removeprefix(f"{test_root}/").split("/")
    if not parts:
        return None

    candidate = f"{test_root}/{parts[0]}"
    if (cfg.repo_root / candidate).is_dir():
        return candidate
    if len(parts) == 1 and parts[0].endswith(".py"):
        return test_root
    return None


def _changed_lane_test_dir(filepath: str, cfg: ProjectConfig) -> str | None:
    for lane in load_lanes().values():
        for lane_root in lane.paths:
            normalized_root = lane_root.rstrip("/")
            if not _path_matches(filepath, normalized_root):
                continue

            relative = filepath.removeprefix(f"{normalized_root}/")
            if relative == filepath or not relative:
                return normalized_root if (cfg.repo_root / normalized_root).is_dir() else None

            first_part = relative.split("/", 1)[0]
            if first_part and not first_part.endswith(".py"):
                candidate = f"{normalized_root}/{first_part}"
                if (cfg.repo_root / candidate).is_dir():
                    return candidate
            return normalized_root if (cfg.repo_root / normalized_root).is_dir() else None
    return None


def _path_matches(path_text: str, root: str) -> bool:
    normalized = root.rstrip("/")
    return path_text == normalized or path_text.startswith(f"{normalized}/")


def _enforce_blast_radius(test_dirs: list[str], cfg: ProjectConfig) -> None:
    if not test_dirs:
        return

    for lane in load_lanes().values():
        if lane.blast_radius_ratio is None:
            continue

        all_dirs = _lane_child_dirs(cfg, lane)
        if not all_dirs:
            continue

        touched = _touched_lane_dirs(cfg, lane, test_dirs, all_dirs)
        if touched and len(touched) / len(all_dirs) > lane.blast_radius_ratio:
            print(
                f"BLOCKED: auto scope touches too much of the {lane.name} lane.",
                file=sys.stderr,
            )
            print(f"Use dev test --lane={lane.name} instead.", file=sys.stderr)
            raise SystemExit(2)


def _lane_child_dirs(cfg: ProjectConfig, lane: LaneConfig) -> set[str]:
    dirs: set[str] = set()
    for root in lane.paths:
        root_path = cfg.repo_root / root
        if not root_path.is_dir():
            continue
        dirs.update(
            path.relative_to(cfg.repo_root).as_posix()
            for path in root_path.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
    return dirs


def _touched_lane_dirs(
    cfg: ProjectConfig,
    lane: LaneConfig,
    test_dirs: list[str],
    all_dirs: set[str],
) -> set[str]:
    lane_roots = {path.rstrip("/") for path in lane.paths}
    broad_roots = set(cfg.test_roots) | lane_roots
    if any(path.rstrip("/") in broad_roots for path in test_dirs):
        return set(all_dirs)

    touched: set[str] = set()
    for test_dir in test_dirs:
        if lane_from_path(test_dir) != lane.name:
            continue
        child = _lane_child_for_path(test_dir, lane)
        if child in all_dirs:
            touched.add(child)
    return touched


def _lane_child_for_path(path_text: str, lane: LaneConfig) -> str:
    normalized_path = path_text.rstrip("/")
    for root in lane.paths:
        normalized_root = root.rstrip("/")
        if normalized_path == normalized_root:
            return normalized_root
        if not normalized_path.startswith(f"{normalized_root}/"):
            continue
        relative = normalized_path.removeprefix(f"{normalized_root}/")
        first_part = relative.split("/", 1)[0]
        return f"{normalized_root}/{first_part}" if first_part else normalized_root
    return normalized_path
