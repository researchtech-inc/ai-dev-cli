"""Bedrock validation for quality-surface delegation."""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_dev_cli._project import find_repo_root

QUALITY_TOOLS = (
    "pytest",
    "ruff",
    "basedpyright",
    "pyright",
    "mypy",
    "semgrep",
    "vulture",
    "interrogate",
)

QUALITY_TOOL_RE = re.compile(rf"(?<![\w./-])(?P<tool>{'|'.join(QUALITY_TOOLS)})(?=$|[\s:;|&),-])")
PYTHON_MODULE_TOOL_RE = re.compile(
    rf"(?<![\w./-])python(?:\d(?:\.\d+)?)?\s+-m\s+(?P<tool>{'|'.join(QUALITY_TOOLS)})(?=$|[\s:;|&),-])"
)
PYTHON_PIP_INSTALL_RE = re.compile(r"(?<![\w./-])python(?:\d(?:\.\d+)?)?\s+-m\s+pip\s+install(?:\s|$)")
SHELL_OPERATORS = ("&&", "||", ";", "|")


@dataclass(frozen=True, slots=True)
class DelegationViolation:
    """One quality-surface delegation violation."""

    path: Path
    line: int
    tool: str

    def format(self, root: Path) -> str:
        relative = self.path.relative_to(root).as_posix()
        return f"{relative}:{self.line}: DELEGATION-RAW-QUALITY-TOOL: raw {self.tool} invocation must delegate to dev"

    def block_message(self, root: Path) -> str:
        """Format the violation as a user-facing block."""
        relative = self.path.relative_to(root).as_posix()
        return (
            f"BLOCKED: {relative}:{self.line} invokes raw {self.tool} in a quality surface. "
            "Use dev <subcommand> instead."
        )


def _active_text(line: str) -> str:
    return line.partition("#")[0].strip()


def _raw_tool(text: str) -> str | None:
    for segment in _shell_segments(text):
        tool = _raw_tool_in_segment(segment)
        if tool is not None:
            return tool
    return None


def _raw_tool_in_segment(segment: str) -> str | None:
    pip_match = PYTHON_PIP_INSTALL_RE.search(segment)
    module_match = PYTHON_MODULE_TOOL_RE.search(segment)
    match = QUALITY_TOOL_RE.search(segment)
    raw_matches = [(item.start(), item.group("tool")) for item in (module_match, match) if item is not None]
    if not raw_matches:
        return None

    raw_start, tool = min(raw_matches, key=lambda item: item[0])
    if pip_match is not None and pip_match.start() <= raw_start:
        return None
    return tool


def _shell_segments(text: str) -> tuple[str, ...]:
    segments: list[str] = []
    segment: list[str] = []
    quote: str | None = None
    index = 0

    while index < len(text):
        char = text[index]
        if quote is not None:
            segment.append(char)
            if char == quote:
                quote = None
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            segment.append(char)
            index += 1
            continue

        operator = _operator_at(text, index)
        if operator is not None:
            _append_segment(segments, segment)
            segment = []
            index += len(operator)
            continue

        segment.append(char)
        index += 1

    _append_segment(segments, segment)
    return tuple(segments)


def _operator_at(text: str, index: int) -> str | None:
    for operator in SHELL_OPERATORS:
        if text.startswith(operator, index):
            return operator
    return None


def _append_segment(segments: list[str], segment: list[str]) -> None:
    text = "".join(segment).strip()
    if text:
        segments.append(text)


def _scan_lines(path: Path, lines: tuple[tuple[int, str], ...]) -> list[DelegationViolation]:
    violations: list[DelegationViolation] = []
    for line_number, line in lines:
        text = _active_text(line)
        if not text:
            continue
        tool = _raw_tool(text)
        if tool is not None:
            violations.append(DelegationViolation(path=path, line=line_number, tool=tool))
    return violations


def _scan_pre_commit(path: Path) -> list[DelegationViolation]:
    lines = tuple(path.read_text(encoding="utf-8").splitlines())
    command_lines = tuple(
        (line_number, line)
        for line_number, line in enumerate(lines, 1)
        if _active_text(line).startswith(("entry:", "id:"))
    )
    return _scan_lines(path, command_lines)


def _scan_makefile(path: Path) -> list[DelegationViolation]:
    lines = path.read_text(encoding="utf-8").splitlines()
    command_lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, 1):
        if line.startswith("\t"):
            command_lines.append((line_number, line))
            continue
        target, separator, command = line.partition(";")
        if separator and ":" in target:
            command_lines.append((line_number, command))
    return _scan_lines(path, tuple(command_lines))


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scan_workflow(path: Path) -> list[DelegationViolation]:
    lines = path.read_text(encoding="utf-8").splitlines()
    command_lines: list[tuple[int, str]] = []
    block_indent: int | None = None

    for line_number, line in enumerate(lines, 1):
        text = _active_text(line)
        if not text:
            continue

        indent = _line_indent(line)
        if block_indent is not None:
            if indent <= block_indent:
                block_indent = None
            else:
                command_lines.append((line_number, text))
                continue

        command_text = text[2:].lstrip() if text.startswith("- ") else text
        if not command_text.startswith("run:"):
            continue

        command = command_text.removeprefix("run:").strip()
        if command in {"|", ">"}:
            block_indent = indent
        elif command:
            command_lines.append((line_number, command))

    return _scan_lines(path, tuple(command_lines))


def _surface_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    pre_commit = root / ".pre-commit-config.yaml"
    if pre_commit.is_file():
        files.append(pre_commit)

    makefile = root / "Makefile"
    if makefile.is_file():
        files.append(makefile)

    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        files.extend(sorted(path for path in workflows.glob("*.yml") if path.is_file()))

    return tuple(files)


def validate(root: Path) -> list[DelegationViolation]:
    """Validate that project-owned quality surfaces delegate to dev."""
    violations: list[DelegationViolation] = []
    for path in _surface_files(root):
        if path.name == ".pre-commit-config.yaml":
            violations.extend(_scan_pre_commit(path))
        elif path.name == "Makefile":
            violations.extend(_scan_makefile(path))
        else:
            violations.extend(_scan_workflow(path))
    return sorted(violations, key=lambda item: (item.path.relative_to(root).as_posix(), item.line, item.tool))


def main() -> int:
    """Run the quality-surface delegation validator."""
    root = find_repo_root(Path.cwd())
    violations = validate(root)
    for violation in violations:
        sys.stdout.write(f"{violation.block_message(root)}\n")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
