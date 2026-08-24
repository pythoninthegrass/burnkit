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
uv add "burnkit @ git+https://github.com/pythoninthegrass/burnkit"
```

Or, for a single-file `uv run --script` driver:

```python
# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "burnkit @ git+https://github.com/pythoninthegrass/burnkit",
# ]
# ///
```

Iterate against a local checkout without re-tagging:

```bash
uv run --with-editable ~/git/burnkit --script ./scripts/burn/driver.py status
```

## `burn-dsh-log`

Reads a [DeepSeek Harness](https://www.deepseek.com/harness/en/) headless-session JSONL log (zstd-compressed, streamed once) so a finished run can be audited rather than trusted — the same anti-fabrication posture the rest of burnkit enforces (see Trust classes above), applied after the fact to whatever a `dsh --profile headless` session actually did.

```bash
uv tool install "burnkit[forensics] @ git+https://github.com/pythoninthegrass/burnkit"
burn-dsh-log <session>.jsonl.zstd types
```

The `forensics` extra is required for every subcommand below, not only `raw`/`search` — the whole CLI shapes output through jq's C bindings, which is why it's an extra rather than a base dependency.

Session logs live under `~/.dsh/sessions/<project-dir>/<session-id>/session.jsonl.zstd`, where `<project-dir>` is dsh's own path-encoding of the working directory the agent ran in (slashes and most punctuation collapsed into the directory name) — that encoding is dsh's, not burnkit's, so don't assume a fixed prefix when scripting against it.

A typical audit pass, cheapest question first:

```bash
# 1. Orient: how big is this run, what kinds of events does it have?
burn-dsh-log session.jsonl.zstd types

# 2. Read what the agent said it was doing, turn by turn.
burn-dsh-log session.jsonl.zstd assistant --limit 20

# 3. Check what it actually ran, not just what it narrated.
burn-dsh-log session.jsonl.zstd joined --name bash --limit 5

# 4. Confirm or refute a specific claim, e.g. did a gate ever fail?
burn-dsh-log session.jsonl.zstd search 'FAIL|error' -i --type tool/result
```

Subcommands:

| Command | What it's for |
| --- | --- |
| `types` | Histogram of `.type` values across the log. Run this first — it orients you to the shape of the run (how many tool calls, turns, reasoning chunks) before you drill into any one thing. |
| `calls [--name NAME] [--turn N] [--step N] [--limit N]` | List `tool/call` events — tool name plus parsed arguments — optionally filtered to one tool, turn, or step. |
| `joined [--name NAME] [--limit N]` | Stream-join each `tool/call` with its matching `tool/result` by `callId`, so you see the command *and* its output together. A call whose result never arrived (killed agent, disk full) is silently omitted rather than treated as an error — that's expected on a truncated log. |
| `assistant [--reasoning] [--limit N]` | The assistant's own text, one line per turn/step. Add `--reasoning` to include its thinking-trace segments as well as final text. |
| `user [--full] [--limit N]` | The prompts sent to the agent. Previews at 200 chars by default; `--full` prints the whole thing — useful for re-reading the exact task prompt a run was given. |
| `search PATTERN [-i] [--type TYPE] [--limit N]` | Regex over the raw JSON of each decoded line. Use this when you're hunting for a marker or error string and don't know (or don't want to assume) which event type carries it. |
| `raw FILTER [-r] [--limit N]` | Apply an arbitrary jq filter to every event, for anything the shaped subcommands above don't cover. `-r` prints a string result unquoted instead of re-encoding it as JSON. |

Every subcommand also takes `--limit N`.

A `joined --name bash` record looks like this (fields present, values illustrative):

```json
{"turn": 1, "step": 2, "name": "bash", "arguments": {"command": "ls -la src/ scripts/ docs/; cat taskfile.yml", "description": "Inspect repo structure"}, "output": "docs/:\ntotal 8\n...\n"}
```

`assistant`/`joined` disagreeing — the narrative says one thing, the actual commands and their output say another — is exactly the mismatch worth catching before trusting a `TASK:DONE`.

## Development

```bash
task test      # uv run pytest
task lint      # ruff check --fix
task format    # ruff format
task scrub     # fail if a consumer-private identifier leaked into this public repo
```

## License

MIT
