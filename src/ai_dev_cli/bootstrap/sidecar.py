"""Managed sidecar reading, writing, hashing, and path classification."""

import hashlib
import json
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ai_dev_cli.bootstrap.profiles import TemplateMarker

SIDECAR_NAME = ".ai-dev-cli-managed"


@dataclass(frozen=True, slots=True)
class ManagedFile:
    """One sidecar-managed file entry."""

    path: str
    template_id: str
    template_version: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Sidecar:
    """Parsed `.ai-dev-cli-managed` data."""

    profile: str
    ai_dev_cli_version: str
    written_at: str
    files: tuple[ManagedFile, ...]


@dataclass(frozen=True, slots=True)
class Classification:
    """Current ownership state for one repository-relative path."""

    path: str
    state: str
    current_sha256: str | None = None
    expected_sha256: str | None = None


def sidecar_path(root: Path) -> Path:
    """Return the managed sidecar path for a repository root."""
    return root / SIDECAR_NAME


def now_timestamp() -> str:
    """Return an informational UTC timestamp for sidecar writes."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_sidecar(root: Path) -> Sidecar | None:
    """Read the sidecar if it exists."""
    path = sidecar_path(root)
    if not path.is_file():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    files = tuple(
        ManagedFile(
            path=str(item["path"]),
            template_id=str(item["template_id"]),
            template_version=int(item["template_version"]),
            sha256=str(item["sha256"]),
        )
        for item in data.get("files", ())
        if (
            isinstance(item, dict)
            and "path" in item
            and "template_id" in item
            and "template_version" in item
            and "sha256" in item
        )
    )
    return Sidecar(
        profile=str(data.get("profile", "")),
        ai_dev_cli_version=str(data.get("ai_dev_cli_version", "")),
        written_at=str(data.get("written_at", "")),
        files=files,
    )


def write_sidecar(root: Path, data: Sidecar) -> None:
    """Write sidecar data as deterministic TOML."""
    lines = [
        f"profile = {_toml_string(data.profile)}",
        f"ai_dev_cli_version = {_toml_string(data.ai_dev_cli_version)}",
        f"written_at = {_toml_string(data.written_at)}",
        "",
    ]
    for item in sorted(data.files, key=lambda entry: entry.path):
        lines.extend((
            "[[files]]",
            f"path = {_toml_string(item.path)}",
            f"template_id = {_toml_string(item.template_id)}",
            f"template_version = {item.template_version}",
            f"sha256 = {_toml_string(item.sha256)}",
            "",
        ))
    sidecar_path(root).write_text("\n".join(lines), encoding="utf-8")


def build_sidecar(
    *,
    profile: str,
    ai_dev_cli_version: str,
    files: tuple[ManagedFile, ...],
    written_at: str | None = None,
) -> Sidecar:
    """Build sidecar data for a successful bootstrap operation."""
    return Sidecar(
        profile=profile,
        ai_dev_cli_version=ai_dev_cli_version,
        written_at=written_at or now_timestamp(),
        files=tuple(sorted(files, key=lambda item: item.path)),
    )


def sha256_bytes(data: bytes) -> str:
    """Return a SHA256 digest for bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    """Return a SHA256 digest for one file."""
    return sha256_bytes(path.read_bytes())


def classify_path(root: Path, data: Sidecar | None, relative_path: str) -> Classification:
    """Classify a path as absent, managed-clean, managed-divergent, or unmanaged."""
    path = normalize_path(relative_path)
    target = root / path
    managed = _file_map(data).get(path) if data is not None else None
    if not target.exists():
        return Classification(path=path, state="absent", expected_sha256=managed.sha256 if managed else None)
    if managed is None:
        return Classification(path=path, state="unmanaged", current_sha256=sha256_path(target))

    current = sha256_path(target)
    if current == managed.sha256:
        return Classification(path=path, state="managed-clean", current_sha256=current, expected_sha256=managed.sha256)
    return Classification(path=path, state="managed-divergent", current_sha256=current, expected_sha256=managed.sha256)


def normalize_path(path: str | Path) -> str:
    """Normalize a repository-relative path to POSIX form."""
    text = Path(path).as_posix()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def managed_file_hashes(root: Path, markers: tuple[TemplateMarker, ...]) -> tuple[ManagedFile, ...]:
    """Build sidecar file entries for generated targets that exist."""
    result = []
    for marker in sorted(markers, key=lambda item: item.path):
        path = normalize_path(marker.path)
        target = root / path
        if target.is_file():
            result.append(
                ManagedFile(
                    path=path,
                    template_id=marker.template_id,
                    template_version=marker.template_version,
                    sha256=sha256_path(target),
                )
            )
    return tuple(result)


def replace_files(data: Sidecar, files: tuple[ManagedFile, ...]) -> Sidecar:
    """Return sidecar data with a replacement managed-file list."""
    return Sidecar(
        profile=data.profile,
        ai_dev_cli_version=data.ai_dev_cli_version,
        written_at=now_timestamp(),
        files=tuple(sorted(files, key=lambda item: item.path)),
    )


def _file_map(data: Sidecar | None) -> dict[str, ManagedFile]:
    if data is None:
        return {}
    return {item.path: item for item in data.files}


def _toml_string(value: str) -> str:
    return json.dumps(value)
