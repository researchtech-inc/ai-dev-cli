"""Profile loading and template rendering for `dev init`."""

import sys
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

SHIPPED_PROFILES = frozenset({"minimal", "strict-python", "agent"})
SUBSTITUTIONS = ("{{ project_name }}", "{{ python_version }}")


class ProfileError(Exception):
    """Profile loading error with a ready-to-render block message."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    """One template source and target from a profile manifest."""

    source: str
    target: str
    merge: str


@dataclass(frozen=True, slots=True)
class TemplateMarker:
    """Template marker metadata for one generated target."""

    path: str
    template_id: str
    template_version: int


@dataclass(frozen=True, slots=True)
class Profile:
    """Loaded bootstrap profile."""

    name: str
    version: int
    root: Path | Traversable
    tool_dev_cli: dict[str, Any]
    templates: tuple[TemplateSpec, ...]


def load_profile(profile_name: str | None, profile_file: str | None) -> Profile:
    """Load a shipped profile or a user-owned profile file."""
    if profile_name and profile_file:
        raise ProfileError(
            "BLOCKED: --profile and --profile-file cannot be used together. Use one profile source instead."
        )
    if profile_file:
        return load_profile_file(Path(profile_file))
    return load_shipped_profile(profile_name or "agent")


def load_shipped_profile(name: str) -> Profile:
    """Load a shipped profile from package resources."""
    if name not in SHIPPED_PROFILES:
        choices = ", ".join(sorted(SHIPPED_PROFILES))
        raise ProfileError(f"BLOCKED: unknown profile '{name}'. Use one of {choices} instead.")
    root = files("ai_dev_cli").joinpath("_templates", name)
    return _load_profile_from_root(root)


def load_profile_file(path: Path) -> Profile:
    """Load a user profile from a profile directory or profile.toml path."""
    if path.is_file() and path.name != "profile.toml":
        raise ProfileError(
            f"BLOCKED: profile file '{path.as_posix()}' is not profile.toml. Use a profile.toml path instead."
        )
    root = path if path.is_dir() else path.parent
    manifest = root / "profile.toml" if path.is_dir() else path
    if not manifest.is_file():
        raise ProfileError(
            f"BLOCKED: profile file '{path.as_posix()}' is missing. Use a readable profile.toml instead."
        )
    return _load_profile_from_root(root)


def render_template(
    profile: Profile,
    template: TemplateSpec,
    *,
    project_name: str,
    python_version: str | None = None,
) -> bytes:
    """Render one profile template using the two allowed substitutions."""
    source_path = profile.root.joinpath(template.source)
    text = source_path.read_text(encoding="utf-8")
    values = {
        "{{ project_name }}": project_name,
        "{{ python_version }}": python_version or f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    for token, value in values.items():
        text = text.replace(token, value)
    return text.encode("utf-8")


def expected_targets(profile: Profile) -> tuple[str, ...]:
    """Return all repository-relative paths owned by a profile."""
    return tuple(template.target for template in profile.templates)


def template_by_target(profile: Profile) -> dict[str, TemplateSpec]:
    """Return profile templates keyed by target path."""
    return {template.target: template for template in profile.templates}


def template_markers(profile: Profile) -> tuple[TemplateMarker, ...]:
    """Return generated target marker metadata for a profile."""
    return tuple(
        TemplateMarker(
            path=template.target,
            template_id=f"{profile.name}/{template.source}",
            template_version=profile.version,
        )
        for template in profile.templates
    )


def _load_profile_from_root(root: Path | Traversable) -> Profile:
    manifest = root.joinpath("profile.toml")
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(
            f"BLOCKED: invalid profile at '{_display_root(root)}'. Use a readable profile.toml instead."
        ) from exc

    name = data.get("name")
    version = data.get("version")
    tool_dev_cli = data.get("tool", {}).get("dev-cli")
    raw_templates = data.get("templates")
    if not isinstance(name, str) or not isinstance(version, int):
        raise ProfileError(
            f"BLOCKED: invalid profile at '{_display_root(root)}'. Use name and version in profile.toml instead."
        )
    if not isinstance(tool_dev_cli, dict):
        raise ProfileError(f"BLOCKED: invalid profile '{name}'. Use a [tool.dev-cli] manifest table instead.")
    if not isinstance(raw_templates, list):
        raise ProfileError(f"BLOCKED: invalid profile '{name}'. Use [[templates]] manifest entries instead.")

    templates = tuple(_parse_template(name, item) for item in raw_templates)
    for template in templates:
        if not root.joinpath(template.source).is_file():
            raise ProfileError(
                f"BLOCKED: profile '{name}' references missing template '{template.source}'. "
                "Use a complete profile directory instead."
            )

    return Profile(name=name, version=version, root=root, tool_dev_cli=tool_dev_cli, templates=templates)


def _parse_template(profile_name: str, raw: object) -> TemplateSpec:
    if not isinstance(raw, dict):
        raise ProfileError(
            f"BLOCKED: invalid template entry in profile '{profile_name}'. Use source and target instead."
        )
    source = raw.get("source")
    target = raw.get("target")
    merge = raw.get("merge", "replace-managed")
    if not isinstance(source, str) or not isinstance(target, str) or not isinstance(merge, str):
        raise ProfileError(
            f"BLOCKED: invalid template entry in profile '{profile_name}'. Use string source and target instead."
        )
    return TemplateSpec(source=source, target=target, merge=merge)


def _display_root(root: Path | Traversable) -> str:
    return root.as_posix() if isinstance(root, Path) else str(root)
