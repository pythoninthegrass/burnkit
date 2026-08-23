# AGENTS.md

This file provides guidance to AI agents when working with code in this repository.

## What this repository is

`burnkit` is the shared driver for overnight headless-agent burn loops, extracted
from three copy-adapted implementations that had drifted apart. See
[`README.md`](README.md) for what it does and how consumers install it.

The library is deliberately narrow. It targets one stack — Backlog.md task
files, per-task git worktrees, a local model endpoint, a remote planner, and
`task <target>` gates on a single box — and should not grow toward "any
overnight workload". Resist generalizing past the consumers that actually exist.

## This repository is public; its consumers are not

**The single most important rule here.** burnkit is public. At least one
consumer is a private repository whose contents may not be published to any
external service. Nothing that names a consumer project, its task-id namespace,
its asset or analysis trees, its reviewer, or its private tooling may land in
this repo — in source, tests, fixtures, docstrings, comments, or docs.

Every such value is supplied by the consumer through `BurnConfig` or a
registered hook. It is never a burnkit default.

Generic *concepts* are fine and worth keeping: "some worktree content must never
reach a remote planner" is a legitimate policy this library implements. Naming
what that content is, is not.

`tests/test_scrub.py` enforces this mechanically. Run `task scrub` before any
commit that adds prose, and treat a failure as "this value belongs in
`BurnConfig`", not as "loosen the pattern list".

## Architecture

One frozen `BurnConfig` dataclass is the entire seam between burnkit and a
consumer. If a value or behavior is project-specific it becomes a config field
or a registered callable — never a conditional inside burnkit.

Three abstractions carry the variation:

- **`Backend`** — how an agent session is launched. Policy flags plus
  callables, no class hierarchy. `Backend.remote_planner` is the flag that
  gates remote-planner-only preflight checks; it is the real semantic, not a
  proxy for anything about worktree contents.
- **`MachineGate`** — a named command plus an optional `applies(changed_paths)`
  predicate. `applies=None` means always. This reconciles consumers that run a
  fixed gate list against consumers that select gates from the diff.
- **`PreflightHook`** — `check(worktree) -> list[str]` returning offenders. A
  non-empty list aborts the launch. This is how a consumer enforces a
  content-boundary that burnkit has no vocabulary for.
- **`Integration`** — the publish strategy (`prepare` → `publish` → `retire`).
  Per-task pull request, or fast-forward into a shared branch.

## Hard rules

- **No `pkill`, at any scope, ever.** Process termination goes through a
  pidfile and `os.killpg(os.getpgid(pid), SIGKILL)`. An unscoped `pkill`
  pattern already killed an unrelated session on a shared box; that regression
  is the reason this library exists. A test asserts the string is absent.
- **The marker protocol is single-sourced.** The prose an agent reads and the
  parser that matches it are generated from the same constants. Never hand-write
  a marker string in a prompt fragment.
- **Never edit a gate to relax what counts as a pass.** No widening an accepted
  set, no treating a zero-case result as evidence, no loosening a validator so a
  stuck task can proceed. If something genuinely cannot be verified, that is a
  bail condition for a human to resolve — not a gate to soften.
- **Trust classes are not decorative.** An agent's own report is
  `agent_attested` and never satisfying on its own. Only gates burnkit ran and
  observed produce `measured_local`.
- **Comments explain WHAT or WHY**, never "improved"/"new"/"fixed". Several
  comments in this codebase encode reasons that took real debugging to learn —
  don't remove one unless you can prove it false.

## Common commands

```bash
task test      # uv run pytest
task lint      # ruff check --fix --respect-gitignore
task format    # ruff format --respect-gitignore
task scrub     # public-repo boundary check
task pre-commit
```

## Conventions

- Python pinned `>=3.13,<3.14`; tool versions in `.tool-versions` via mise.
- pytest, plain `def test_...() -> None:` functions with descriptive names.
- Tests are hermetic: no git, no network, no model endpoints, no agent
  processes. The live loop is exercised by hand on the box that runs it.
- Formatting and lint config live in `ruff.toml`, not `pyproject.toml`.
- Conventional Commits. Author `pythoninthegrass`. **No co-author trailers.**

## Context7

Use Context7 MCP for library/API documentation, code generation, and setup or
configuration steps, without being explicitly asked.

### Libraries

- astral-sh/uv
- astral-sh/ruff
- j178/prek
- mrlesk/backlog.md
- websites/taskfile_dev
