# ai-dev-cli

## What it is

`ai-dev-cli` is the executable contract between AI agents and the team's Python coding standards. One CLI (`dev`),
three-tier policy (in-package bedrock, profile-copied defaults, project extras), hook-enforced.

## Install

Python 3.14+ is required.

```shell
pip install ai-dev-cli
```

## Quick start

Write a profile to the project:

```shell
dev init
```

Show resolved config, lanes, and hooks:

```shell
dev info
```

Run unit tests:

```shell
dev test --lane=unit
```

Run the full quality pipeline:

```shell
dev check
```

## Subcommand inventory

### Always available

- `info`: Shows resolved config, lanes, checks, hook entrypoint, hook config, and infrastructure status.
- `status`: Shows changed files, affected scopes, prior runs, and the suggested next validation command.
- `format`: Runs Ruff format and Ruff auto-fix, or checks formatting with `--check`.
- `lint`: Runs Ruff check plus Ruff format check; `--fix` delegates to `format`.
- `typecheck`: Runs basedpyright, pyright, or mypy through the resolved project runner.
- `test`: Runs scoped tests or a configured lane, with testmon, xdist, logs, and rerun-failed handling.
- `check`: Runs hard bedrock checks first, then the resolved project quality pipeline.

`deadcode`, `semgrep`, `docstrings`, and `test-validator` are check-pipeline step names, not subcommands.
Run them through `dev check` or `dev check --steps <name>`.

### Config-gated

- `verify`: Runs the configured validation sequence when `[tool.dev-cli.verify].enabled = true`.
- `benchmark`: Runs configured benchmark smoke, case, or tier targets when benchmark config is enabled.
- `examples`: Runs configured static, smoke, or live example checks when examples config is enabled.

### Scaffolding

- `init`: Copies the selected profile into the project and wires quality surfaces to `dev`.
- `init --upgrade --plan/--apply/--recover`: Plans, applies, or recovers profile migration work.

## Profiles

- `minimal`: Smallest useful `[tool.dev-cli]` shape, lanes, hook wiring, and delegation surfaces.
- `strict-python`: Strict Python defaults for Ruff, basedpyright, interrogate, Vulture, lanes, and starter Semgrep.
- `agent`: Strict Python profile plus explicit agent-oriented lane permissions.

Downstream projects bring their own framework-specific profile, such as an ai-pipeline-core profile; none ship in
`ai-dev-cli`.

## Hook

`python -m ai_dev_cli.hook` is the canonical hook entry point. `dev init` wires it into both
`.claude/settings.json` and `.codex/config.toml`. The hook blocks raw `pytest`/`ruff`/`pyright`/`mypy`, dangerous
`dev` modes, and other bypass shapes.

## Runtime trust onboarding (for agents)

Agents reading this should treat `dev` as the single canonical surface. Raw quality tools are blocked at the hook for
a reason; use `dev <subcommand>` exclusively, and consult `dev info` for the resolved config.

## Standards (bedrock)

- Python 3.14+.
- Line length 120.
- Ruff strict set.
- basedpyright strict.
- Frozen Pydantic V2 for cross-boundary carriers.
- Async-only public I/O.
- No `__import__()`.
- No `warnings.warn(... DeprecationWarning)`.
- Module-scoped replayable classes.
- Docstring coverage 100%.
- Vulture min-confidence 80.

## Reporting bugs / contributing

Open a GitHub issue. PRs welcome. CI runs `dev check` + `dev test --lane=unit` +
`dev test --lane=integration` on every push.

## License

See [LICENSE](LICENSE).
