"""Idempotency detection via file content hashing."""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import xxhash

import ai_dev_cli
from ai_dev_cli import _project

STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PreviousRun:
    command: str
    timestamp: str
    exit_code: int
    summary: str
    log_file: str


@dataclass(frozen=True, slots=True)
class StepCacheEntry:
    """Cached result for a cacheable dev check step."""

    input_hash: str
    tool_version: str
    result: str
    log_file: str
    timestamp: str


def _read_state_file() -> dict[str, Any]:
    cfg = _project.load_config()
    if not cfg.state_file.exists():
        return _empty_state(cfg.config_hash, ai_dev_cli.__version__)
    try:
        data = json.loads(cfg.state_file.read_text(encoding="utf-8"))
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return _empty_state(cfg.config_hash, ai_dev_cli.__version__)
    if not isinstance(data, dict):
        return _empty_state(cfg.config_hash, ai_dev_cli.__version__)
    if (
        data.get("schema_version") != STATE_SCHEMA_VERSION
        or data.get("tool_version") != ai_dev_cli.__version__
        or data.get("config_hash") != cfg.config_hash
    ):
        return _empty_state(cfg.config_hash, ai_dev_cli.__version__)
    data.setdefault("runs", {})
    data.setdefault("step_cache", {})
    data.setdefault("rerun_failed", {})
    return data


def _write_state_file(data: dict[str, Any]) -> None:
    cfg = _project.load_config()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = STATE_SCHEMA_VERSION
    data["tool_version"] = ai_dev_cli.__version__
    data["config_hash"] = cfg.config_hash
    data.setdefault("runs", {})
    data.setdefault("step_cache", {})
    data.setdefault("rerun_failed", {})
    fd, tmp_path = tempfile.mkstemp(dir=str(cfg.runs_dir), suffix=".state.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, cfg.state_file)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _empty_state(config_hash: str, tool_version: str) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "tool_version": tool_version,
        "config_hash": config_hash,
        "runs": {},
        "step_cache": {},
        "rerun_failed": {},
    }


def _update_file_hash(hasher: xxhash.xxh64, repo_root: Path, filepath: Path) -> None:
    try:
        rel = filepath.relative_to(repo_root).as_posix()
    except ValueError:
        rel = filepath.as_posix()
    try:
        data = filepath.read_bytes()
    except OSError:
        return
    hasher.update(rel.encode())
    hasher.update(b"\0")
    hasher.update(data)
    hasher.update(b"\0")


def _update_text_hash(hasher: xxhash.xxh64, label: str, value: str) -> None:
    hasher.update(label.encode())
    hasher.update(b"\0")
    hasher.update(value.encode())
    hasher.update(b"\0")


def _iter_path_files(path: Path, tracked_extensions: frozenset[str]) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix in tracked_extensions else []
    if not path.exists():
        return []
    return [
        filepath for filepath in sorted(path.rglob("*")) if filepath.is_file() and filepath.suffix in tracked_extensions
    ]


def _uv_pip_list_digest(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["uv", "pip", "list", "--format", "json"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=20,
            check=False,
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
    ):
        return ""
    if result.returncode != 0:
        return ""
    return xxhash.xxh64(result.stdout.encode()).hexdigest()


def _hash_configured_inputs(hasher: xxhash.xxh64, cfg: Any) -> None:
    for pattern in cfg.hash_globs:
        for filepath in sorted(cfg.repo_root.glob(pattern)):
            if filepath.is_file():
                _update_file_hash(hasher, cfg.repo_root, filepath)

    for filename in (*cfg.hash_files, *cfg.hash_optional_files):
        filepath = cfg.repo_root / filename
        if filepath.is_file():
            _update_file_hash(hasher, cfg.repo_root, filepath)


def hash_tracked_files(*paths: str) -> str:
    """Hash tracked inputs under paths plus configured extras, environment, and tool versions."""
    cfg = _project.load_config()

    hasher = xxhash.xxh64()
    for path_text in sorted(set(paths)):
        path = cfg.repo_root / path_text
        for filepath in _iter_path_files(path, cfg.tracked_extensions):
            _update_file_hash(hasher, cfg.repo_root, filepath)

    _hash_configured_inputs(hasher, cfg)
    _update_text_hash(hasher, "ai-dev-cli", ai_dev_cli.__version__)
    _update_text_hash(hasher, "config", cfg.config_hash)
    _update_text_hash(hasher, "python", ".".join(str(part) for part in sys.version_info[:3]))
    _update_text_hash(hasher, "uv-pip-list", _uv_pip_list_digest(cfg.repo_root))
    return hasher.hexdigest()


def git_changed_files() -> list[str]:
    """Get files changed relative to HEAD (staged + unstaged + untracked)."""
    cfg = _project.load_config()

    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        cwd=cfg.repo_root,
        check=False,
    )
    files = result.stdout.strip().splitlines() if result.returncode == 0 else []

    result2 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=cfg.repo_root,
        check=False,
    )
    if result2.returncode == 0:
        files.extend(result2.stdout.strip().splitlines())

    configured_files = frozenset((*cfg.hash_files, *cfg.hash_optional_files))
    return [f for f in files if any(f.endswith(ext) for ext in cfg.tracked_extensions) or f in configured_files]


def load_previous_run(command_key: str) -> PreviousRun | None:
    """Load previous run state for a given command key."""
    try:
        data = _read_state_file()
        r = data.get("runs", {}).get(command_key)
        if r is None:
            return None
        return PreviousRun(
            command=r["command"],
            timestamp=r["timestamp"],
            exit_code=r["exit_code"],
            summary=r["summary"],
            log_file=r["log_file"],
        )
    except (json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"WARN  Ignoring corrupt state file: {e}\n")
        return None


def save_run_state(command_key: str, command: str, exit_code: int, summary: str, log_file: str, file_hash: str) -> None:
    """Save run state for idempotency detection (atomic write)."""
    data = _read_state_file()
    runs = data.setdefault("runs", {})
    runs[command_key] = {
        "command": command,
        "timestamp": datetime.now(UTC).isoformat(),
        "exit_code": exit_code,
        "summary": summary,
        "log_file": log_file,
        "file_hash": file_hash,
    }
    data.setdefault("step_cache", {})
    _write_state_file(data)


def check_idempotency(command_key: str, file_hash: str) -> PreviousRun | None:
    """Check if a command was already run with the same file hash."""
    try:
        data = _read_state_file()
        r = data.get("runs", {}).get(command_key)
        if r is None:
            return None
        if r.get("file_hash") == file_hash:
            return PreviousRun(
                command=r["command"],
                timestamp=r["timestamp"],
                exit_code=r["exit_code"],
                summary=r["summary"],
                log_file=r["log_file"],
            )
    except (json.JSONDecodeError, KeyError) as e:
        sys.stderr.write(f"WARN  Ignoring corrupt state file: {e}\n")
    return None


def load_step_cache(step_name: str) -> StepCacheEntry | None:
    """Load a cached dev check step result."""
    data = _read_state_file()
    raw = data.get("step_cache", {}).get(step_name)
    if not isinstance(raw, dict):
        return None
    try:
        return StepCacheEntry(
            input_hash=raw["input_hash"],
            tool_version=raw["tool_version"],
            result=raw["result"],
            log_file=raw["log_file"],
            timestamp=raw["timestamp"],
        )
    except KeyError:
        return None


def save_step_cache(step_name: str, input_hash: str, tool_version: str, result: str, log_file: str) -> None:
    """Persist a cacheable dev check step result."""
    data = _read_state_file()
    step_cache = data.setdefault("step_cache", {})
    step_cache[step_name] = {
        "input_hash": input_hash,
        "tool_version": tool_version,
        "result": result,
        "log_file": log_file,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    data.setdefault("runs", {})
    _write_state_file(data)


def load_failure_dependency_hash(nodeid: str) -> str | None:
    """Return the dependency digest recorded when a nodeid last failed."""
    data = _read_state_file()
    raw = data.get("rerun_failed", {}).get(nodeid)
    if not isinstance(raw, dict):
        return None
    value = raw.get("dependency_hash")
    return value if isinstance(value, str) else None


def save_failure_dependency_hashes(items: dict[str, str]) -> None:
    """Persist dependency digests for failing nodeids."""
    if not items:
        return
    data = _read_state_file()
    rerun_failed = data.setdefault("rerun_failed", {})
    timestamp = datetime.now(UTC).isoformat()
    for nodeid, dependency_hash in items.items():
        rerun_failed[nodeid] = {
            "dependency_hash": dependency_hash,
            "timestamp": timestamp,
        }
    data.setdefault("runs", {})
    data.setdefault("step_cache", {})
    _write_state_file(data)
