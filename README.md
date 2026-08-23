# burnkit

Shared driver for overnight headless-agent burn loops: point it at a
[Backlog.md](https://github.com/MrLesk/Backlog.md) task queue and it works the
backlog unattended — one task per git worktree, one agent session per task,
gated before anything is published.

It exists because the same driver had been copy-adapted across three repos, and
fixes only ever flowed forward. A bug fixed in the newest copy stayed broken in
the older ones, including one that sent `pkill` after a pattern broad enough to
kill unrelated sessions on the same box.

## What burnkit owns

- **Queue** — Backlog.md frontmatter, dependency-directory precedence,
  readiness and leaf selection, persisted attempt counts that survive a restart.
- **Lifecycle** — the `BURN` directory layout, `KILL` sentinel, heartbeat log,
  handled-task ledger, fast-fail detection, exit codes.
- **Backends** — a launch backend is a frozen dataclass of policy flags plus
  callables, not a class hierarchy. Two ship in the box.
- **Marker protocol** — the `TASK:DONE` / `TASK:BAIL` contract, single-sourced
  so the prose the agent reads is generated from the same constants the parser
  matches.
- **Proof-of-done gates** — machine gates, acceptance-criteria enforcement, a
  diff-scope gate, and trust-class labeling that refuses to let an agent mint
  its own evidence.

## What the consumer owns

Everything project-shaped, through one `BurnConfig`: models and endpoints,
repository paths and slug, task-id prefix, gate commands, code-path prefixes,
prompt prose, preflight hooks, and the publish strategy.

Every field, the `Backend` and integration contracts, the marker protocol, the
`burn_dir` layout, exit codes, and both sets of `BURN_*` variables:
[`docs/configuration.md`](docs/configuration.md).

## Trust classes

A gate result carries where it came from. `measured_local` means burnkit ran
the machine gates itself and watched them pass. `agent_attested` means the
agent said so and nothing independent confirmed it — recorded, surfaced with an
explicit human-confirmation banner, never treated as satisfying on its own.

## Install

```bash
uv add "burnkit @ git+https://github.com/pythoninthegrass/burnkit@v0.1.0"
```

Or, for a single-file `uv run --script` driver:

```python
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "burnkit @ git+https://github.com/pythoninthegrass/burnkit@v0.1.0",
# ]
# ///
```

Iterate against a local checkout without re-tagging:

```bash
uv run --with-editable ~/git/burnkit --script ./scripts/burn/driver.py status
```

## `burn-dsh-log`

Reads a headless-session JSONL log (zstd-compressed, streamed once) so a
finished run can be audited rather than trusted. Install the extra for the
`raw`/`search` subcommands, which evaluate user-supplied jq filters:

```bash
uv tool install "burnkit[forensics] @ git+https://github.com/pythoninthegrass/burnkit@v0.1.0"
burn-dsh-log session.jsonl.zstd types
```

## Development

```bash
task test      # uv run pytest
task lint      # ruff check --fix
task format    # ruff format
task scrub     # fail if a consumer-private identifier leaked into this public repo
```

## License

MIT
