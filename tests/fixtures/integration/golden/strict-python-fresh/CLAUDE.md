<!--
template_id = "strict-python/CLAUDE.md"
template_version = 1
-->

# Project Guidance

Use `dev` for validation and workflow commands.

- Prefer `dev status` before choosing a test scope.
- Use `dev test --lane=unit` for the default test lane.
- Use `dev check` for the strict Python quality pipeline.
- Keep pre-commit, Makefile, and CI quality targets delegated to `dev`.
- When a command is blocked, follow the replacement command in the `BLOCKED:` message.
