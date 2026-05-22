"""Subprocess execution with output capture and summary formatting."""

import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_dev_cli import _project, _rerun, _state

MAX_INLINE_FAILURES = 5
MAX_RUN_FILE_AGE_SECONDS = 86400


_cleanup_done = False


def _cleanup_old_runs() -> None:
    """Remove run artifacts older than 24 hours. Best-effort, silent."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True

    cfg = _project.load_config()
    if not cfg.runs_dir.is_dir():
        return

    cutoff = time.time() - MAX_RUN_FILE_AGE_SECONDS
    deleted_any = False

    for entry in cfg.runs_dir.iterdir():
        if entry.name == ".state.json":
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                deleted_any = True
        except OSError:
            continue

    if deleted_any:
        _prune_stale_state_entries(cfg)


def _prune_stale_state_entries(cfg: _project.ProjectConfig) -> None:
    """Remove state entries whose log files no longer exist on disk."""
    if not cfg.state_file.exists():
        return
    try:
        data = json.loads(cfg.state_file.read_text(encoding="utf-8"))
        runs = data.get("runs", {})
        pruned = {k: v for k, v in runs.items() if (cfg.repo_root / v.get("log_file", "")).exists()}
        if len(pruned) < len(runs):
            data["runs"] = pruned
            cfg.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except (
        json.JSONDecodeError,
        OSError,
    ):
        pass


BEDROCK_STEP_TIMEOUT_KEYS = {
    "bedrock-semgrep": "semgrep",
    "bedrock-test-validator": "test-validator",
}


def run_command(
    cmd: list[str] | tuple[str, ...],
    label: str,
    *,
    log_suffix: str | None = None,
    use_reportlog: bool = False,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
    slow_threshold: float | None = None,
) -> tuple[int, str, str, bool]:
    """Run a command, capture output to file, print summary.

    Returns (exit_code, summary_line, log_file_relative_path, is_collection_failure).
    """
    _cleanup_old_runs()

    cfg = _project.load_config()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    suffix = log_suffix or label.replace(" ", "-")
    log_filename = f"{now:%Y%m%d-%H%M%S}-{suffix}.log"
    log_path = cfg.runs_dir / log_filename
    rel_log = str(log_path.relative_to(cfg.repo_root))

    report_path = cfg.runs_dir / f"{now:%Y%m%d-%H%M%S}-{suffix}.jsonl" if use_reportlog else None

    full_cmd = list(cmd)
    if use_reportlog and report_path:
        full_cmd.extend(["--report-log", str(report_path)])

    try:
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"$ {' '.join(full_cmd)}\n")
            f.write(f"# timestamp: {now.isoformat()}\n\n")
            f.flush()
            result = subprocess.run(
                full_cmd,
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cfg.repo_root,
                env=run_env,
                timeout=timeout_seconds,
                check=False,
            )
            f.write(f"\n# exit code: {result.returncode}\n")
    except subprocess.TimeoutExpired:
        timeout_text = str(timeout_seconds) if timeout_seconds is not None else "unknown"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n# timeout after {timeout_text}s\n")
        summary = (
            f"FAIL  {label} — timeout after {timeout_text}s\n"
            f"  Details: {rel_log}\n"
            "  Next step:   inspect the log and rerun the dev command"
        )
        return 124, summary, rel_log, False
    except FileNotFoundError:
        tool_name = full_cmd[0]
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n# file not found: {tool_name}\n")
        summary = (
            f"FAIL  {label} — '{tool_name}' not found\n"
            f"  Details: {rel_log}\n"
            f"  Command was: {' '.join(full_cmd)}\n"
            f"  Next step:   install {tool_name} or check PATH, then rerun the dev command"
        )
        return 127, summary, rel_log, False

    log_text = log_path.read_text(encoding="utf-8", errors="replace")

    if use_reportlog and report_path and report_path.exists():
        summary, is_collection_failure = _summarize_pytest_report(
            report_path,
            result.returncode,
            rel_log,
            log_text,
            slow_threshold,
        )
    else:
        summary = _summarize_generic(result, label, rel_log, log_text)
        is_collection_failure = False

    return result.returncode, summary, rel_log, is_collection_failure


def run_check_step(step_name: str, commands: tuple[tuple[str, ...], ...], *, cacheable: bool) -> int:
    """Run a dev check step, using the per-step cache for safe steps."""
    cfg = _project.load_config()
    started = time.monotonic()
    input_hash = _state.hash_tracked_files(*cfg.source_packages, *cfg.test_roots)
    tool_version = _tool_version_for_commands(commands) if cacheable else ""
    if cacheable:
        cached = _state.load_step_cache(step_name)
        if (
            cached is not None
            and cached.input_hash == input_hash
            and cached.tool_version == tool_version
            and cached.result == "PASS"
            and (cfg.repo_root / cached.log_file).exists()
        ):
            print(f"SKIP  {step_name} (cached)")
            print(f"  Details: {cached.log_file}")
            return 0

    last_log_file = ""
    timeout_seconds = _step_timeout_seconds(step_name, cfg.step_timeouts)
    for cmd_tuple in commands:
        exit_code, summary, log_file, _ = run_command(
            list(cmd_tuple),
            step_name,
            log_suffix=f"check-{step_name}",
            timeout_seconds=timeout_seconds,
        )
        last_log_file = log_file
        if exit_code != 0:
            print(summary)
            return exit_code

    if cacheable:
        _state.save_step_cache(step_name, input_hash, tool_version, "PASS", last_log_file)
    elapsed = time.monotonic() - started
    print(f"PASS  {step_name} ({elapsed:.1f}s)")
    print(f"  Details: {last_log_file}")
    return 0


def _step_timeout_seconds(step_name: str, timeouts: dict[str, int]) -> int | None:
    key = BEDROCK_STEP_TIMEOUT_KEYS.get(step_name, step_name.replace("_", "-"))
    return timeouts.get(key)


def _tool_version_for_commands(commands: tuple[tuple[str, ...], ...]) -> str:
    parts: list[str] = []
    for command in commands:
        version_cmd = _version_command(command)
        try:
            result = subprocess.run(version_cmd, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            parts.append(f"{' '.join(version_cmd)}: {exc}")
            continue
        parts.append(f"{' '.join(version_cmd)}: {result.stdout.strip() or result.stderr.strip()}")
    return "\n".join(parts)


def _version_command(command: tuple[str, ...]) -> list[str]:
    parts = list(command)
    if not parts:
        return []
    if len(parts) >= 3 and parts[0] in {"uv", "poetry"} and parts[1] == "run":
        return [parts[0], parts[1], parts[2], "--version"]
    return [parts[0], "--version"]


_DESELECTED_RE = re.compile(r"(\d+) deselected")


def _parse_deselected_count(stdout: str) -> int:
    """Extract deselected count from pytest stdout."""
    match = _DESELECTED_RE.search(stdout)
    return int(match.group(1)) if match else 0


def _summarize_pytest_report(
    report_path: Path,
    exit_code: int,
    log_file: str,
    stdout: str = "",
    slow_threshold: float | None = None,
) -> tuple[str, bool]:
    """Summarize pytest results from reportlog JSONL."""
    passed = 0
    failed = 0
    errors = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    collect_errors: list[str] = []
    slow_tests: list[tuple[str, float]] = []
    total_duration = 0.0
    first_start: float | None = None
    last_stop: float | None = None

    for line in report_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        report_type = entry.get("$report_type")

        if report_type == "TestReport" and entry.get("when") == "call":
            outcome = entry.get("outcome", "")
            duration = entry.get("duration", 0.0)
            total_duration += duration

            start = entry.get("start")
            stop = entry.get("stop")
            if start is not None and (first_start is None or start < first_start):
                first_start = start
            if stop is not None and (last_stop is None or stop > last_stop):
                last_stop = stop

            if outcome == "passed":
                passed += 1
            elif outcome == "failed":
                failed += 1
                failures.append({
                    "nodeid": entry.get("nodeid", "?"),
                    "message": _extract_failure_message(entry),
                })
            elif outcome == "skipped":
                skipped += 1

            if slow_threshold is not None and duration >= slow_threshold:
                slow_tests.append((entry.get("nodeid", "?"), duration))

        elif report_type == "TestReport" and entry.get("when") == "setup" and entry.get("outcome") == "failed":
            errors += 1
            failures.append({
                "nodeid": entry.get("nodeid", "?"),
                "message": f"SETUP ERROR: {_extract_failure_message(entry)}",
            })

        elif report_type == "CollectReport" and entry.get("outcome") == "failed":
            nodeid = entry.get("nodeid", "?")
            msg = _extract_failure_message(entry)
            collect_errors.append(f"{nodeid}: {msg}" if msg else nodeid)

    total = passed + failed + errors + skipped
    deselected = _parse_deselected_count(stdout)
    wall_time = (last_stop - first_start) if first_start is not None and last_stop is not None else total_duration
    duration_str = f"{wall_time:.1f}s"

    if failed:
        _rerun.record_failure_dependency_hashes(
            _project.load_config().repo_root,
            tuple(str(f["nodeid"]) for f in failures),
        )

    if total == 0 and exit_code != 0:
        lines = [f"FAIL  test — collection failed (exit code {exit_code})"]
        lines.append(f"  Details: {log_file}")
        if collect_errors:
            lines.append("")
            lines.extend(f"  COLLECT ERROR: {err}" for err in collect_errors[:MAX_INLINE_FAILURES])
            if len(collect_errors) > MAX_INLINE_FAILURES:
                lines.append(f"  ... and {len(collect_errors) - MAX_INLINE_FAILURES} more")
        lines.append("")
        lines.append("  Next step:   fix collection errors and rerun dev test")
        return "\n".join(lines), True

    parts = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if errors:
        parts.append(f"{errors} errors")
    if skipped:
        parts.append(f"{skipped} skipped")
    counts = ", ".join(parts) if parts else f"{total} tests"

    lines: list[str] = []

    if exit_code == 0:
        if deselected:
            lines.append(f"PASS  test — {counts}, {deselected} unchanged (testmon) in {duration_str}")
        else:
            lines.append(f"PASS  test — {counts} in {duration_str}")
        lines.append(f"  Details: {log_file}")
        if slow_tests:
            slow_tests.sort(key=lambda x: -x[1])
            lines.append("")
            lines.append(f"  Slow tests (>{_format_seconds(slow_threshold)}s):")
            for nodeid, dur in slow_tests[:MAX_INLINE_FAILURES]:
                lines.append(f"    {nodeid} ({dur:.1f}s)")
            if len(slow_tests) > MAX_INLINE_FAILURES:
                lines.append(f"    ... and {len(slow_tests) - MAX_INLINE_FAILURES} more")
    else:
        lines.append(f"FAIL  test — {counts} in {duration_str}")
        lines.append(f"  Details: {log_file}")
        lines.append("")
        for f in failures[:MAX_INLINE_FAILURES]:
            lines.append(f"  FAIL {f['nodeid']}")
            if f["message"]:
                lines.append(f"       {f['message']}")
        if len(failures) > MAX_INLINE_FAILURES:
            lines.append(f"  ... and {len(failures) - MAX_INLINE_FAILURES} more failures")
        lines.append("")
        lines.append(f"  Next step:   {_pytest_failure_next_step(failed)}")

    return "\n".join(lines), False


def _format_seconds(value: float | None) -> str:
    if value is None:
        return "0"
    seconds = float(value)
    return str(int(seconds)) if seconds.is_integer() else f"{seconds:.1f}"


def _extract_failure_message(entry: dict[str, Any]) -> str:
    """Extract a short failure message from a TestReport entry."""
    longrepr = entry.get("longrepr", "")
    if isinstance(longrepr, str):
        for line in reversed(longrepr.splitlines()):
            stripped = line.strip()
            if stripped.startswith("E "):
                return stripped[2:].strip()[:120]
        for line in longrepr.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:120]
    elif isinstance(longrepr, dict):
        crash = longrepr.get("reprcrash", {})
        return crash.get("message", "")[:120]
    return ""


def _pytest_failure_next_step(failed: int) -> str:
    if failed > 0:
        return "dev test --rerun-failed"
    return "fix setup errors and rerun dev test"


def _summarize_generic(
    result: subprocess.CompletedProcess[str],
    label: str,
    log_file: str,
    output: str = "",
) -> str:
    """Summarize a non-pytest command result."""
    if result.returncode == 0:
        return f"PASS  {label}\n  Details: {log_file}"

    lines = [f"FAIL  {label} (exit code {result.returncode})"]
    lines.append(f"  Details: {log_file}")

    output_lines = [line for line in output.splitlines() if line.strip() and not line.startswith(("$ ", "# "))]
    if output_lines:
        lines.append("")
        lines.extend(f"  {line.rstrip()}" for line in output_lines[-5:])

    lines.append("")
    lines.append("  Next step:   inspect the log, fix the failure, and rerun the dev command")
    return "\n".join(lines)


MAX_WORST_FILES = 5


def format_coverage_summary(runs_dir: Path, scoped: bool) -> str | None:
    """Parse coverage.json and return a concise summary."""
    cov_file = runs_dir / "coverage.json"
    if not cov_file.is_file():
        return None

    data = json.loads(cov_file.read_text(encoding="utf-8"))
    total_pct = data["totals"]["percent_covered"]

    files = [(path, info["summary"]["percent_covered"]) for path, info in data["files"].items()]
    files.sort(key=lambda x: x[1])
    worst = files[:MAX_WORST_FILES]

    lines: list[str] = []
    if scoped:
        lines.append(f"  Coverage: {total_pct:.1f}% (scoped run — threshold not enforced)")
    else:
        lines.append(f"  Coverage: {total_pct:.1f}%")

    if worst:
        lines.append("  Lowest:")
        for path, pct in worst:
            lines.append(f"    {path} ({pct:.0f}%)")

    lines.append(f"  HTML: {runs_dir / 'coverage-html' / 'index.html'}")
    return "\n".join(lines)


def print_skip(label: str, previous_timestamp: str, previous_exit_code: int, log_file: str) -> int:
    """Print skip message when no code changed since last run. Returns previous exit code."""
    status = "PASS" if previous_exit_code == 0 else "FAIL"
    print(f"SKIP  {label} — no changes since last run ({status} at {previous_timestamp})")
    print(f"  Details: {log_file}")
    if previous_exit_code != 0:
        print("  Next step:   dev test --rerun-failed")
    else:
        print("  All tests already verified. No action needed.")
    return previous_exit_code
