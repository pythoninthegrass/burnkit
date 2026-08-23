# Configuration reference

Everything a consumer supplies, in one place. burnkit itself reads no
environment variables and no module state: a consumer resolves its own
environment and hands the result over as a single `BurnConfig`.

## The shim shape

There is deliberately no `burn` console script. `run_from_cli` needs a
`BurnConfig` only the consumer can supply, so a consumer's driver stays a
single executable file:

```python
#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.13,<3.14"
# dependencies = [
#     "python-decouple>=3.8",
#     "burnkit @ git+https://github.com/pythoninthegrass/burnkit@v0.1.0",
# ]
# ///

from burnkit import BurnConfig, MachineGate, PullRequestPerTask, hermes_backend, run_from_cli
from decouple import config
from pathlib import Path

CONFIG = BurnConfig(project="myproject", burn_dir=Path.home() / "burn", repo=Path.home() / "git/myproject", ...)
BACKENDS = {"hermes": hermes_backend(CONFIG)}

if __name__ == "__main__":
    run_from_cli(CONFIG, PullRequestPerTask(CONFIG), BACKENDS)
```

That gives the consumer `driver.py {run,resume,status,kill}`. `run` takes
`--backend`, `--once`, and `--task`; `resume` takes `--backend`.

## `BurnConfig`

Required, in positional order:

| Field | Meaning |
| --- | --- |
| `project` | Slug used in heartbeats and log lines |
| `burn_dir` | Root of the run's state directory (see [Layout](#layout)) |
| `repo` | The consumer's own checkout; worktrees are cut from it |

### Publication

| Field | Default | Meaning |
| --- | --- | --- |
| `repo_slug` | `""` | `owner/name`, for a strategy that opens pull requests |
| `base_branch` | `"main"` | Branch every attempt is cut from and published to |
| `base_remote` | `"origin"` | Remote holding `base_branch`. **Empty** for a branch that deliberately never leaves the machine — there is then no remote-tracking ref, and asking for one fails every task |
| `reviewer` | `""` | Requested on an opened pull request |
| `author` | `""` | Named in the generated git instructions the agent reads |

### Task queue

| Field | Default | Meaning |
| --- | --- | --- |
| `task_id_prefix` | `""` | e.g. `"PROJ"`; drives marker text and branch names |
| `example_task_id` | `""` | A real id, shown in the prompt so the agent sees the shape |
| `tasks_dir` | `"backlog/tasks"` | Live task files, repo-relative |
| `dep_dirs` | `("backlog/completed", "backlog/archive/tasks")` | Where finished tasks are filed. Still count as dependencies. Order is precedence: a live task in `tasks_dir` wins over a same-id copy here, and earlier entries win over later ones |
| `skip_list` | `frozenset()` | Task ids the queue never hands out — one that needs human subdivision first |

### Models and endpoints

| Field | Default | Meaning |
| --- | --- | --- |
| `model` / `provider` | `""` | The planner |
| `builder_model` / `builder_provider` | `""` | The delegated implementer, if the agent has one |
| `fallback_model` / `fallback_provider` | `""` | Planner to demote to after three consecutive fast failures. **Empty disables demotion** — a stale override sentinel cannot redirect a run that never opted in |
| `lemonade_health_url` | local `/api/v0/health` | Only used by the `preflight_lemonade` helper |
| `secrets_env` | `None` | `.env` file the launch secrets are read from |
| `dsh_env_file` | `None` | `.env` sourced inside a dsh session |
| `default_backend` | `"dsh"` | Used when `--backend` is absent |
| `launch_secrets` | `{}` | Secret name → fallback value, resolved from `secrets_env` into the launched agent's environment |
| `health_check` | `None` | `(backend_name) -> bool`; returning False aborts the run before any task. burnkit never learns what was checked |

### Loop limits

| Field | Default | Meaning |
| --- | --- | --- |
| `task_timeout_s` | `3600` | Wall-clock ceiling for one agent session |
| `max_attempts` | `2` | Attempts per task before it is left for triage |
| `max_turns` | `150` | Passed to the agent |
| `fast_fail_s` | `90` | A run dying faster than this is counted separately — it suggests provider trouble, not task trouble |
| `poll_s` | `15` | Log-polling interval while waiting for a session to exit |

### Gating

| Field | Default | Meaning |
| --- | --- | --- |
| `code_change_prefixes` | `()` | A diff touching one of these makes the task a *code* task, which must clear the machine gates. Use `ANY_PATH` for "every task is a code task" |
| `code_task_allowed_prefixes` | `()` | Where a code task's diff is allowed to land; anything outside is a scope violation. Use `ANY_PATH` to disable the scope gate |
| `machine_gates` | `()` | See [`MachineGate`](#machinegate) |
| `preflight_hooks` | `()` | See [`PreflightHook`](#preflighthook) |
| `close_out_phase_parents` | `False` | Mark a phase parent Done once every leaf under it is. Appropriate for a consumer publishing onto a shared branch; a pull-request-per-task consumer usually wants a human to close the parent out |

`ANY_PATH` is `("",)` — the empty-prefix tuple, exported by name because an
empty tuple means the *opposite* in both selectors: no path matches.

### Prompt

| Field | Default | Meaning |
| --- | --- | --- |
| `prompt_project_fragment` | `None` | The consumer's own prose: role line, hard rules, verification bar |
| `prompt_backend_fragments` | `{}` | Per-backend fragment paths. Defaults to `prompt_header.<backend>.txt` beside the project fragment |
| `context7_line` | `""` | One line about documentation tooling available to the agent |
| `extra_bail_conditions` | `()` | Appended to burnkit's own bail conditions |
| `launch_env` | `lambda task, backend: {}` | Extra environment for a launched agent. Takes the backend name, because out-of-tree content is usually only kept out-of-tree for the backends whose planning step leaves the machine |

## `Backend`

A launch backend is a frozen dataclass of policy flags plus callables — no
class hierarchy. `hermes_backend(config, ...)` and `dsh_backend(config, ...)`
build the two that ship; a consumer can build its own.

| Field | Meaning |
| --- | --- |
| `name` | Key in the `BACKENDS` dict and the `--backend` value |
| `remote_planner` | Whether the planning step leaves the machine. This is the real semantic — `remote_planner_only` preflight hooks are gated on it, and nothing about worktree contents is implied |
| `shell_cmd` | The one-shot shell the launcher runs. Must write `$$` to `$BURN_PIDFILE` (see [Termination](#termination)) and append the exit marker to `$BURN_LOG` |
| `task_env` | `(task, prompt_file, log) -> dict`; the environment `shell_cmd` reads |
| `prepare_worktree` | `(task, worktree) -> None`, run after checkout. `symlink_prepare` for read-only content, `copy_prepare` when a gate may write to it — a link would let a gate reach back into the consumer's own checkout |
| `launch_line` | One line describing the model posture, spliced into the prompt |
| `prompt_fragment` | This backend's prompt fragment path |

Both shipped backends override the planner per launch, so a run demoted to its
fallback planner mid-queue launches the very next task on the demoted one.

## `MachineGate`

```python
MachineGate(name="test", argv=("task", "test"), applies=None, timeout_s=180)
```

Run by burnkit in the driver, not by the agent — an agent's report of its own
gate run is not evidence. `applies=None` means unconditional; a consumer that
selects gates from the diff passes a predicate over the changed paths instead.
Both styles produce the same `GateReport`.

`timeout_s` matters more than it looks. A lint pass and a full replay run
cannot share one ceiling without either killing the slow gate or letting a hung
fast one hold the whole queue. Each gate runs in its own process group, and a
timeout comes back as a *failed gate* (exit code 124, as GNU `timeout` uses)
rather than an exception — one hung gate fails its own task instead of ending
the overnight run. The group kill reaches helpers the gate spawned without ever
matching on a process name.

## `PreflightHook`

```python
PreflightHook(name="no-private-content", check=lambda wt: [...], remote_planner_only=True)
```

`check(worktree)` returns offenders; a non-empty list aborts the launch with
exit code 5. This is how a consumer enforces a content boundary burnkit has no
vocabulary for. Set `remote_planner_only` when the boundary is about what
leaves the machine, so a fully-local backend is not asked to satisfy a rule
that exists to keep content away from a third party.

## Integration strategies

An integration is a single method. Branch creation and branch retirement are
`cli`'s job, not the strategy's — the strategy only decides where a passing
attempt goes:

```python
def publish(
    self,
    task: str,
    title: str,
    branch: str,          # the attempt branch, already cut and committed
    wt: Path,             # the attempt's worktree
    *,
    trust: str = TRUST_AGENT_ATTESTED,
    report: GateReport | None = None,
    launch_line: str | None = None,
) -> str | None:          # the published ref, or None if publication failed
```

Returning `None` is a failure signal, not a no-op: `cli` treats an
unpublished-but-verified attempt as a failed attempt and lets it retry.
Anything a strategy needs beyond these arguments comes off `self.config`.

Two ship:

- **`PullRequestPerTask(config)`** — pushes the attempt branch to `origin`,
  appends a `review-queue.md` line, then opens a pull request against
  `config.base_branch` via `gh` with `config.reviewer` requested. Returns the
  branch name. Nothing here touches the consumer's own checkout.
- **`FastForwardBranch(config, branch="burn", push=True)`** — moves a shared
  branch onto the attempt, refusing anything that is not a fast-forward (a
  diverged shared branch means someone else published in between). Implemented
  as an ancestry check plus a ref move rather than `git merge --ff-only`, so it
  does not depend on what the consumer's repo has checked out. `push=False`
  keeps the branch local. Returns the shared branch name.

Both are frozen dataclasses whose first field is the `BurnConfig`, and both
carry a `name` field for logging. A consumer can supply its own object with the
same `publish` signature instead — there is no base class to subclass.

Branch retirement is the module-level `retire_branch(repo, branch, base_sha,
task, attempt, *, published=False)`, called by `cli` after every attempt. A
failed attempt that still committed real work is *renamed* to
`rescue/<task>.a<n>` rather than force-deleted, because only the reflog
survives `git branch -D` and that is not enough to recover it before the next
`git gc`. `published=True` skips the rescue: the commits are already reachable
from whatever was published, so a rescue ref would only accumulate one dead
branch per success.

## Marker protocol

The prose the agent reads and the parser that scans its log are generated from
the same constants in `burnkit.prompt`. Never hand-write a marker string into a
prompt fragment.

| Constant | Value |
| --- | --- |
| `prompt.DONE_TEMPLATE` | `=== TASK:DONE {task} ===` |
| `prompt.BAIL_TEMPLATE` | `=== TASK:BAIL {task} reason={reason} ===` |
| `prompt.TASK_FILE_SEPARATOR` | `=== TASK FILE ===` |
| `proc.EXIT_MARKER` | `=== AGENT_EXIT:` |

The exit marker lives in `proc` rather than `prompt` because a `shell_cmd`
appends it, not the agent.

A bail is a first-class outcome, not a failure to hide: an agent that cannot
finish honestly is worth more than one that forces a result past a gate.

burnkit generates four prompt sections from these — task status, git, bail
conditions, and finish — because they describe the protocol between the agent
and this library. The consumer owns its role line, hard rules, verification
instructions, and documentation pointer; those are hand-tuned against real runs
and burnkit does not touch them.

## Verification order

`verify()` runs, stopping at the first failure:

1. bail marker present → bail
2. done marker present
3. at least one new commit beyond the base SHA
4. every acceptance criterion in the task file checked
5. *(code tasks)* diff stays within `code_task_allowed_prefixes`
6. *(code tasks)* every applicable machine gate green

Clearing 1–4 earns `agent_attested`. Only 5–6, run and observed by burnkit,
earn `measured_local`.

## Termination

Process termination goes through a pidfile and
`os.killpg(os.getpgid(pid), SIGKILL)`. **No `pkill`, at any scope, ever** — an
unscoped pattern already killed an unrelated session on a shared box, and that
regression is why this library exists. A test asserts the string is absent from
`burnkit/proc.py`.

This is a contract on the backend too: a `shell_cmd` that does not write `$$`
to `$BURN_PIDFILE` leaves nothing to scope a kill to, and a name search is then
the only option left.

## Layout

`burn_dir` holds all run state, so a run survives a restart:

| Path | Contents |
| --- | --- |
| `KILL` | Sentinel; the loop stops when it appears |
| `status/state.log` | Append-only progress record |
| `mirror/` | Detached worktree at the base tip, read for the queue so the consumer's own checkout is never touched |
| `handled.txt` | Tasks this run has already taken a turn on |
| `attempts.json` | Persisted attempt counts |
| `review-queue.md` | One line per published task, tagged with its trust class |
| `PLANNER_OVERRIDE` | Set when a run demotes its planner |
| `pids/` | One pidfile per launched task |
| `logs/` | Session logs, with `current` symlinked to the live one |
| `prompts/` | The composed prompt handed to each task |
| `wt/` | One git worktree per task |

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Queue drained, or `--once` finished |
| `1` | The `KILL` sentinel was seen |
| `3` | `health_check` returned False |
| `4` | A task exhausted `max_attempts` |
| `5` | A preflight hook found offenders |

## Environment variables

Two distinct sets, both spelled `BURN_*`, which is worth keeping straight.

**Set by burnkit, read by the launched agent's shell.** A custom `shell_cmd`
consumes these:

| Variable | Set by |
| --- | --- |
| `BURN_PROMPT` | both backends — path to the composed prompt |
| `BURN_LOG` | both backends — path to tee the session into |
| `BURN_PIDFILE` | both backends — where `shell_cmd` must write `$$` |
| `BURN_MODEL` / `BURN_PROVIDER` | hermes — the resolved planner |
| `BURN_MAX_TURNS` | hermes |
| `BURN_DSH_ENV` | dsh — `.env` to source in-session |
| `DSH_PERMISSION_MODE` | dsh — `danger-full-access`, required because a task always runs inside a git worktree whose `.git` metadata lives outside it |

Plus whatever `launch_env(task, backend)` returns, and the `launch_secrets`
resolved from `secrets_env`.

**Read by the consumer's shim, not by burnkit.** These are a convention shared
by the existing consumers rather than an interface — a shim resolves them with
`decouple.config` and passes the results into `BurnConfig`, so the names are
whatever that shim chooses. The established set:

`BURN_DIR`, `BURN_REPO`, `BURN_REPO_SLUG`, `BURN_BASE_BRANCH`,
`BURN_BASE_REMOTE`, `BURN_BRANCH`, `BURN_PUSH`, `BURN_REVIEWER`,
`BURN_SECRETS_ENV`, `BURN_DSH_ENV`, `BURN_MODEL`, `BURN_PROVIDER`,
`BURN_ORCH_MODEL`, `BURN_ORCH_PROVIDER`, `BURN_BUILDER_MODEL`,
`BURN_BUILDER_PROVIDER`, `BURN_FALLBACK_MODEL`, `BURN_FALLBACK_PROVIDER`,
`BURN_LEMONADE_URL`, `BURN_TASK_TIMEOUT_S`, `BURN_MAX_ATTEMPTS`,
`BURN_MAX_TURNS`, `BURN_BACKEND`.

Note the collision: a shim reads `BURN_MODEL` to *configure* the planner, and
hermes' backend *sets* `BURN_MODEL` for the launched agent. They are different
processes, so nothing breaks, but they are not the same variable.
