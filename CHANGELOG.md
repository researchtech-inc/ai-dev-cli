# Changelog

## v0.2.0

- Added `dev probe <name> [pytest_args...]` subcommand.
- Added `[tool.dev-cli.probes.<name>]` config with `enabled`, `target`, `agent_allowed`, and `timeout`.
- Updated CI workflow to install `uv` for `uvx semgrep`.
- Added pre-push hook for `dev test --lane=integration`.

## v0.1.0

- Initial ai-dev-cli artifact release.
