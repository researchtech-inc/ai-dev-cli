"""Apply upgrade disposition rows and recover dropped files."""

from pathlib import Path

from ai_dev_cli.bootstrap import disposition, profiles, sidecar


class ApplyError(Exception):
    """Apply or recover block."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def execute_plan(
    root: Path,
    rows: tuple[disposition.DispositionRow, ...],
    profile: profiles.Profile,
    managed: sidecar.Sidecar,
    *,
    accepted_paths: frozenset[str],
) -> sidecar.Sidecar:
    """Execute add, move, drop, and keep rows from a fresh plan."""
    templates = profiles.template_by_target(profile)
    current = managed
    for row in rows:
        if row.disp == "divergent" and row.path not in accepted_paths:
            raise ApplyError(_divergent_message(row.path))
        if row.action == "add" or (row.disp == "divergent" and row.path in templates):
            _write_profile_target(root, profile, templates[row.path])
        elif row.action == "move":
            _move(root, row.path, row.target, managed, accepted_paths=accepted_paths)
        elif row.action == "drop" and (root / row.path).is_file():
            (root / row.path).unlink()

    managed_files = sidecar.managed_file_hashes(root, profiles.template_markers(profile))
    return sidecar.replace_files(current, managed_files)


def recover_path(
    root: Path,
    relative_path: str,
    managed: sidecar.Sidecar,
) -> sidecar.Sidecar:
    """Adopt one user-restored file into sidecar management."""
    path = sidecar.normalize_path(relative_path)
    target = root / path
    if not target.is_file():
        raise ApplyError(
            f"BLOCKED: path '{path}' is absent from the working tree. Use git diff to restore it first instead."
        )
    previous = next((item for item in managed.files if item.path == path), None)
    managed_files = (
        *tuple(item for item in managed.files if item.path != path),
        sidecar.ManagedFile(
            path=path,
            template_id=previous.template_id if previous is not None else f"{managed.profile}/recovered/{path}",
            template_version=previous.template_version if previous is not None else 1,
            sha256=sidecar.sha256_path(target),
        ),
    )
    return sidecar.replace_files(managed, managed_files)


def _write_profile_target(root: Path, profile: profiles.Profile, template: profiles.TemplateSpec) -> None:
    target = root / template.target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        profiles.render_template(
            profile,
            template,
            project_name=root.name,
        )
    )


def _move(
    root: Path,
    source: str,
    target: str,
    managed: sidecar.Sidecar,
    *,
    accepted_paths: frozenset[str],
) -> None:
    source_path = root / source
    if not source_path.exists():
        return
    target = sidecar.normalize_path(target)
    target_path = root / target
    target_state = sidecar.classify_path(root, managed, target)
    if target_state.state != "absent" and target not in accepted_paths:
        raise ApplyError(_move_target_message(target, target_state.state))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.rename(target_path)


def _divergent_message(path: str) -> str:
    return f"BLOCKED: {path} is divergent and would be overwritten. Use --force --accept {path} instead."


def _move_target_message(path: str, state: str) -> str:
    if state == "managed-divergent":
        return (
            f"BLOCKED: {path} was edited outside ai-dev-cli management. "
            f"Use --force --accept {path} after reviewing git diff instead."
        )
    if state == "unmanaged":
        return (
            f"BLOCKED: {path} is unmanaged and would be overwritten. "
            f"Use --force --accept {path} after backing up the file instead."
        )
    return (
        f"BLOCKED: {path} already exists and would be overwritten. "
        f"Use --force --accept {path} after reviewing git diff instead."
    )
