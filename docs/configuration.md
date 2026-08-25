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
#     "burnkit @ git+https://github.com/pythoninthegrass/burnkit@v0.2.0",
# ]
# ///

from burnkit import BurnConfig, MachineGate, hermes_backend, run_from_cli
from decouple import config
from pathlib import Path

CONFIG = BurnConfig(project="myproject", burn_dir=Path.home() / "burn", repo=Path.home() / "git/myproject", ...)
BACKENDS = {"hermes": hermes_backend(CONFIG)}

if __name__ == "__main__":
    run_from_cli(CONFIG, backends=BACKENDS)
```

That gives the consumer `driver.py {run,resume,status,kill}`. `run` takes
`--backend`, `--once`, and `--task`; `resume` takes `--backend`. Leaving
`integration` unset (as above) gets a consumer the default publish strategy --
see [Integration strategies](#integration-strategies) below. Pass a strategy
positionally (`run_from_cli(CONFIG, PullRequestPerTask(CONFIG), BACKENDS)`) to
opt into something else.

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
| `health_check_url` | local vLLM/llama-swap `/v1/models` | Only used by the `preflight_local_model` helper |
| `secrets_env` | `None` | `.env` file the launch secrets are read from |
| `dsh_env_file` | `None` | `.env` sourced inside a dsh session |
| `default_backend` | `"dsh"` | Used when `--backend` is absent |
| `launch_secrets` | `{}` | Secret name → fallback value, resolved from `secrets_env` into the launched agent's environment |
| `health_check` | `None` | `(backend_name) -> bool`; returning False aborts the run before any task. burnkit never learns what was checked |

### Loop limits

| Field | Default | Meaning |
| --- | --- | --- |
| `task_timeout_s` | `21600` | Wall-clock ceiling for one agent session |
| `max_attempts` | `2` | Attempts per task before it is left for triage; revising the task clears it (see [Attempts and triage](#attempts-and-triage)) |
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
| `stall_check` | Optional `(task, worktree) -> str \| None` — why the run looks stuck. Defaults to `None`; see [No-progress detection](#no-progress-detection) |

Both shipped backends override the planner per launch, so a run demoted to its
fallback planner mid-queue launches the very next task on the demoted one.

### No-progress detection

An agent can get stuck in a loop where every call succeeds: no error fires, the
exit marker never lands, and the run burns the whole `task_timeout_s` re-running
commands whose output cannot have changed. `stall_check` ends that attempt
early. The reason is written to the heartbeat as phase `stall` before the
process group is killed, and the attempt then goes through normal verification —
which fails it for the missing marker, as an incomplete run should.

`dsh_backend` supplies one: it locates the session log by matching the `cwd`
each session records (not by re-deriving dsh's path encoding, which is not
burnkit's to depend on), then flags a run whose last 12 tool calls are ≥75%
repeats of calls already made. Both numbers are `dshlog.STALL_WINDOW` /
`dshlog.STALL_THRESHOLD`, calibrated against a real looping session in which
healthy exploration peaked at a 0.17 repeat ratio while the eight-command cycle
it fell into passed 0.75 by call 49 of 72.

Call identity ignores a free-text `description` argument: in that same session
six byte-identical commands were relabeled "Render ..." then "Re-render ...",
so keying on it would have missed the loop entirely.

`hermes_backend` leaves it `None` — it has no transcript to judge progress from,
and absence of evidence must not read as evidence of a stall. For the same
reason a session log that is missing or mid-write yields `None` rather than a
verdict.

## `MachineGate`

```python
MachineGate(name="test", argv=("task", "test"), applies=None, timeout_s=180, vacuous_if=None)
```

Run by burnkit in the driver, not by the agent — an agent's report of its own
gate run is not evidence. `applies=None` means unconditional; a consumer that
selects gates from the diff passes a predicate over the changed paths instead.
Both styles produce the same `GateReport`.

`vacuous_if` is a predicate over the gate's combined stdout+stderr that
recognises its own "nothing to check" output. An empty suite — no specs written
yet, no sources matching the command's glob — exits 0 having verified nothing,
and without this burnkit records that as a pass:

```python
MachineGate(
    name="diff-verify",
    argv=("task", "diff-verify"),
    vacuous_if=lambda out: "nothing to verify yet" in out,
)
```

A vacuous result is not a failure and does not fail the task, but it does not
count as evidence either. If every applicable gate comes back vacuous (or none
applied), the task still publishes — with `agent_attested` trust and a `no
machine evidence` reason, so it reaches the review queue flagged for a human
rather than wearing a `measured_local` label nothing earned. Vacuity is only
evaluated for a gate that exited 0; a non-zero exit is a real failure however
much its output resembles an empty suite. A gate that leaves `vacuous_if` as
`None` is never vacuous.

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

- **`FastForwardBranch(config, branch="burn", push=True, publish_mirror=None)`**
  — moves a shared branch onto the attempt, refusing anything that is not a
  fast-forward (a diverged shared branch means someone else published in
  between). Implemented as an ancestry check plus a ref move rather than `git
  merge --ff-only`, so it does not depend on what the consumer's repo has
  checked out. `push=False` keeps the branch local. Returns the shared branch
  name. Set `publish_mirror` to a path to route the ref move through a
  dedicated clone instead of `config.repo` directly — needed whenever the
  branch being fast-forwarded (typically `base_branch`) might be checked out
  in the consumer's own interactive working copy; git refuses to force-move or
  fetch into a branch checked out in a sibling worktree of the same repo, but a
  genuinely separate clone has no such restriction.
- **`PullRequestPerTask(config)`** — pushes the attempt branch to `origin`,
  appends a `review-queue.md` line, then opens a pull request against
  `config.base_branch` via `gh` with `config.reviewer` requested. Returns the
  branch name. Nothing here touches the consumer's own checkout.

Both are frozen dataclasses whose first field is the `BurnConfig`, and both
carry a `name` field for logging. A consumer can supply its own object with the
same `publish` signature instead — there is no base class to subclass.

### The default: `default_integration(config)`

Not passing `integration` to `main()`/`run_from_cli()` (or passing `None`)
resolves to `default_integration(config)`: a `FastForwardBranch` fast-forwarding
`config.base_branch` straight through a dedicated mirror clone at
`config.layout.publish_mirror`, never through `config.repo` itself. A PR that
only re-states a gate result the driver already ran tends to get
rubber-stamped rather than genuinely reviewed, so fast-forward — not
`PullRequestPerTask` — is what a consumer gets by not choosing. Pass
`PullRequestPerTask(config)` explicitly to opt back into a PR per task.

Branch retirement is the module-level `retire_branch(repo, branch, base_sha,
task, attempt, *, published=False)`, called by `cli` after every attempt. A
failed attempt that still committed real work is *renamed* to
`rescue/<task>.a<n>` rather than force-deleted, because only the reflog
survives `git branch -D` and that is not enough to recover it before the next
`git gc`. `published=True` skips the rescue: the commits are already reachable
from whatever was published, so a rescue ref would only accumulate one dead
branch per success.

`kill` SIGKILLs the driver mid-loop, so that retirement never runs for the
task it interrupted. `resume` therefore reclaims first: every worktree git
still has registered under `config.layout.worktrees` is removed and its branch
retired against its own fork point, so the branch is free for `run` to cut
again and any commits the killed attempt made land on a rescue ref. Worktrees
elsewhere in the repo are the consumer's own and are left alone.

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
earn `measured_local` — and step 6 only counts when a gate actually checked
something. A code task whose gates all came back vacuous, or whose gates all
filtered out, falls back to `agent_attested`; see
[`vacuous_if`](#machinegate).

## Termination

Process termination goes through a pidfile and
`os.killpg(os.getpgid(pid), SIGKILL)`. **No `pkill`, at any scope, ever** — an
unscoped pattern already killed an unrelated session on a shared box, and that
regression is why this library exists. A test asserts the string is absent from
`burnkit/proc.py`.

This is a contract on the backend too: a `shell_cmd` that does not write `$$`
to `$BURN_PIDFILE` leaves nothing to scope a kill to, and a name search is then
the only option left.

## Attempts and triage

A task that spends `max_attempts` without publishing is left for triage: the
queue stops offering it, `status` lists it under `blocked`, and a run that hits
one exits `4`. That is a request for a human to look, not a finding that the
task is finished — only `Done` and archiving are terminal.

So the state has to be clearable, and the exit is **revising the task**. Each
attempt is recorded against a fingerprint of the task file it ran on, and
`queue.has_budget` treats a task whose definition has changed since it failed
as a different question, with a fresh budget. Editing the task is the gesture
triage asks for anyway, so there is no separate command to remember and no way
to leave a still-open task tombstoned by a counter it cannot shed.

Restarting the run grants nothing on its own — that is deliberate. Attempt
counts persist across process restarts precisely so a supervisor relaunching
after a blocked exit cannot hand out an unlimited budget one level up, and
resetting them on startup would reintroduce exactly that. The bound is also
what stops a single failing task from consuming a whole run: the loop re-picks
the lowest-ordinal ready task every iteration, so without a cap a task that
fails on turn one is picked again immediately, forever.

Counts written before fingerprinting existed are bare integers. They load as an
unknown definition, which cannot be shown to be unchanged, so they earn one
more budget rather than a permanent tombstone — a one-time effect, and the run
re-blocks such a task within that same run if it fails again.

Archiving is terminal by location, not by status: a task file outside
`tasks_dir` is loaded only so dependencies on it resolve, and is never handed
out even if it was archived while still `To Do`.

## Layout

`burn_dir` holds all run state, so a run survives a restart:

| Path | Contents |
| --- | --- |
| `KILL` | Sentinel; the loop stops when it appears |
| `status/state.log` | Append-only progress record |
| `mirror/` | Detached worktree at the base tip, read for the queue so the consumer's own checkout is never touched |
| `handled.txt` | Tasks this run has already taken a turn on |
| `attempts.json` | Persisted attempt counts, each against the fingerprint of the task definition it was spent on |
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
| `4` | A task exhausted `max_attempts` and is left for triage |
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
`BURN_HEALTH_URL`, `BURN_TASK_TIMEOUT`, `BURN_MAX_ATTEMPTS`,
`BURN_MAX_TURNS`, `BURN_BACKEND`.

Note the collision: a shim reads `BURN_MODEL` to *configure* the planner, and
hermes' backend *sets* `BURN_MODEL` for the launched agent. They are different
processes, so nothing breaks, but they are not the same variable.
