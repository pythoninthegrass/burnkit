"""The command-line surface and the main loop.

There is no console script for this: `main()` needs a `BurnConfig` and an
integration strategy that only a consumer repo can supply. A consumer ships a
small executable that builds both and calls `main(config, integration)`.
"""

import argparse
import os
import subprocess
import sys
import time
from burnkit import prompt
from burnkit.backend import Backend, dsh_backend, hermes_backend, preflight_hooks_for, resolve_backend
from burnkit.config import BurnConfig, base_ref
from burnkit.gates import verify
from burnkit.integration import default_integration, retire_branch
from burnkit.proc import git, kill_all, kill_task_processes, launch, launch_env, log_signature, wait_for_exit
from burnkit.queue import branch_name, fingerprint, has_budget, is_phase_parent, load_tasks, next_ready, task_md
from burnkit.state import Attempt, bump_attempts, load_attempts, mark_handled, now, read_fallback_planner, record_fallback_planner
from collections.abc import Callable
from pathlib import Path

EXIT_KILLED = 1
EXIT_HEALTH_CHECK = 3
EXIT_BLOCKED = 4
EXIT_PREFLIGHT_HOOK = 5


def default_backends(config: BurnConfig) -> dict[str, Backend]:
    return {"dsh": dsh_backend(config), "hermes": hermes_backend(config)}


def build_arg_parser(backends: dict[str, Backend], default_backend: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run", help="work the live backlog queue")
    runp.add_argument("--once", action="store_true", help="process a single task then exit")
    runp.add_argument(
        "--task",
        default=None,
        help="only run this specific task id, still subject to the normal readiness checks "
        "(deps done, not skip-listed, attempts remaining) -- for targeted verification runs",
    )
    runp.add_argument(
        "--backend",
        choices=sorted(backends),
        default=None,
        help=f"launch backend (default {default_backend!r})",
    )
    resumep = sub.add_parser("resume", help="clear kill state, requeue stale state, continue")
    resumep.add_argument("--backend", choices=sorted(backends), default=None, help="launch backend for the resumed run")
    sub.add_parser("status", help="print backlog progress + heartbeats")
    sub.add_parser("kill", help="emergency stop: SIGKILL the driver and every agent tree it launched")
    return p


def ensure_mirror(config: BurnConfig) -> None:
    """(Re)point the mirror at the current base-branch tip. Never touches the
    consumer's own checkout.

    check=False on the checkout-triggering calls: a post-checkout hook (e.g.
    git-lfs) failing does not undo the ref/tree update, so treating a nonzero
    hook exit as fatal here would crash a whole overnight run over a cosmetic
    hook problem rather than a real state issue.
    """
    mirror = config.layout.mirror
    ref = base_ref(config)
    if config.base_remote:
        git("fetch", config.base_remote, config.base_branch, cwd=config.repo)
    if not mirror.exists():
        git("worktree", "add", "--detach", str(mirror), ref, cwd=config.repo, check=False)
    else:
        git("checkout", "--detach", ref, cwd=mirror, check=False)


def stall_watch(backend: Backend, task: str, wt: Path, since: float) -> tuple[Callable[[], bool] | None, list[str]]:
    """A `wait_for_exit` stall_check for this backend, plus the list it records
    its reason into.

    None rather than a no-op callable when the backend cannot judge progress, so
    `wait_for_exit` keeps its original behavior instead of polling for nothing.
    `since` is this attempt's launch time, so a leftover transcript from a prior
    attempt in the same worktree path is never mistaken for this attempt's own.
    """
    reasons: list[str] = []
    if backend.stall_check is None:
        return None, reasons

    def check() -> bool:
        if (reason := backend.stall_check(task, wt, since)) is None:
            return False
        reasons.append(reason)
        return True

    return check, reasons


def _remove_worktree(repo: Path, wt: Path) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=False, capture_output=True)


def _registered_worktrees(repo: Path, under: Path) -> list[tuple[Path, str]]:
    """(path, branch) for every worktree git knows about below `under`.

    Read from git rather than globbing the directory: the directory can already
    be gone while the registration and the branch survive, and the names are
    the consumer's task ids, whose case burnkit does not get to assume.
    """
    found: list[tuple[Path, str]] = []
    path: Path | None = None
    for line in git("worktree", "list", "--porcelain", cwd=repo, check=False).stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch ") and path is not None:
            if path.is_relative_to(under):
                found.append((path, line.removeprefix("branch ").removeprefix("refs/heads/")))
            path = None
    return found


def reclaim_worktrees(repo: Path, worktrees: Path, base: str, attempts: dict[str, Attempt]) -> None:
    """Clear the worktrees and attempt branches a killed run left behind.

    `kill` SIGKILLs the driver mid-loop, so the loop tail that retires the
    attempt branch never runs -- and `git worktree add -b` refuses to recreate
    an existing branch, which stopped `resume` dead on the very task the kill
    interrupted. Retiring each leftover the way a finished attempt would keeps
    any commits it managed to make, on a rescue ref.
    """
    for wt, branch in _registered_worktrees(repo, worktrees):
        _remove_worktree(repo, wt)
        # The branch's own fork point, not the current tip: base may have moved
        # on since the attempt was cut, and every such branch would then look
        # like it carried work and earn a rescue ref pointing into base.
        fork = git("merge-base", base, branch, cwd=repo, check=False).stdout.strip()
        retire_branch(repo, branch, fork, wt.name, attempts.get(wt.name, Attempt(0)).n)
    subprocess.run(["git", "worktree", "prune"], cwd=repo, check=False)


def cmd_run(
    config: BurnConfig,
    backend: Backend,
    integration,
    *,
    once: bool = False,
    only_task: str | None = None,
) -> int:
    layout = config.layout
    layout.ensure_dirs()
    (layout.pids / "driver").write_text(str(os.getpid()))
    env = launch_env(config.secrets_env, config.launch_secrets)
    header = prompt.compose(config, backend)
    fast_fails = 0
    ensure_mirror(config)

    while (task := next_ready(config, layout.mirror, only_task=only_task)) is not None:
        if layout.kill_file.exists():
            print("KILL sentinel present; stopping.")
            return EXIT_KILLED
        # Backend-dependent infrastructure the consumer needs up before a launch
        # is worth attempting (a local model server, say). burnkit only knows
        # whether the check passed.
        if config.health_check is not None and not config.health_check(backend.name):
            layout.write_heartbeat(task, 0, "preflight", "HEALTH_CHECK_FAILED")
            return EXIT_HEALTH_CHECK

        ensure_mirror(config)  # refresh to the latest base tip before cutting a new branch
        t = load_tasks(layout.mirror, config.tasks_dir, config.dep_dirs)[task]
        base_sha = git("rev-parse", base_ref(config), cwd=config.repo).stdout.strip()
        branch = branch_name(task, t.title)
        wt = layout.worktrees / task
        _remove_worktree(config.repo, wt)
        git("worktree", "add", "-b", branch, str(wt), base_sha, cwd=config.repo)
        backend.prepare_worktree(task, wt)

        prompt_file = layout.prompts / f"{task}.txt"
        prompt_file.write_text(header + f"\n{prompt.TASK_FILE_SEPARATOR}\n" + task_md(wt, config.tasks_dir, task).read_text())
        attempt = bump_attempts(layout, task, fingerprint(t.file))
        log = layout.logs / f"{task}.a{attempt}.log"
        current = layout.logs / "current"
        current.unlink(missing_ok=True)
        current.symlink_to(log)

        # Refuse to launch over a worktree carrying content the consumer declared
        # off-limits for this backend -- by construction, not trust.
        if offenders := preflight_hooks_for(config, backend, wt):
            layout.write_heartbeat(task, attempt, "abort", f"preflight hook: {offenders}")
            _remove_worktree(config.repo, wt)
            git("branch", "-D", branch, cwd=config.repo, check=False)
            print(f"{task} ABORTED: preflight hook found {offenders}; refusing to launch.")
            return EXIT_PREFLIGHT_HOOK

        layout.write_heartbeat(task, attempt, "start", f"backend={backend.name} branch={branch}")
        # Captured before the launch, because `launch` returns as soon as the
        # agent is spawned: anything already at this path is an earlier
        # attempt's, and `wait_for_exit` must not read its marker as this one's.
        log_baseline = log_signature(log)
        launch_time = time.time()
        launch(
            task,
            wt,
            backend.shell_cmd,
            backend.task_env(task, prompt_file, log),
            env,
            forward=tuple(config.launch_secrets),
        )
        stall_check, stall_reasons = stall_watch(backend, task, wt, launch_time)
        finished, elapsed = wait_for_exit(
            log,
            kill_file=layout.kill_file,
            timeout_s=config.task_timeout_s,
            poll_s=config.poll_s,
            stall_check=stall_check,
            baseline=log_baseline,
        )
        if not finished:
            # Recorded before the kill so a triaging human sees why the attempt
            # ended early rather than inferring it from a missing marker.
            if stall_reasons:
                layout.write_heartbeat(task, attempt, "stall", stall_reasons[0])
            kill_task_processes(layout, task)

        verdict = verify(config, task, wt, log, base_sha, env=env)
        published = None
        reason = verdict.reason
        if verdict.ok:
            published = integration.publish(
                task,
                t.title,
                branch,
                wt,
                trust=verdict.trust,
                report=verdict.report,
                launch_line=backend.launch_line,
            )
            reason = f"{verdict.reason}; published={published}"

        if published:
            # The queue source will not report this task Done until a human
            # merges the published work, so track it here instead.
            mark_handled(layout, task)
            layout.write_heartbeat(task, attempt, "end", f"DONE branch={branch}")
            fast_fails = 0
        else:
            blocked = attempt >= config.max_attempts
            layout.write_heartbeat(task, attempt, "end", f"{'BLOCKED' if blocked else 'FAILED'} {reason[:120]}")
            fast_fails = fast_fails + 1 if elapsed < config.fast_fail_s else 0
            if fast_fails >= 3:
                # Three runs dying inside the fast-fail window is provider
                # trouble, not task trouble. A consumer that declared a fallback
                # planner gets demoted onto it rather than burning the rest of
                # the queue against a sick endpoint.
                if config.fallback_model and read_fallback_planner(layout) is None:
                    record_fallback_planner(layout, config.fallback_model, config.fallback_provider)
                    layout.write_heartbeat(task, attempt, "fallback", f"planner -> {config.fallback_model}")
                else:
                    layout.write_heartbeat(task, attempt, "warn", "3 fast fails in a row -- check agent/model health")
                fast_fails = 0
            if blocked:
                _remove_worktree(config.repo, wt)
                retire_branch(config.repo, branch, base_sha, task, attempt)
                kill_task_processes(layout, task)
                # A blocked task never becomes ready again (attempts exhausted,
                # status still To Do), so the queue would just hand it back.
                # Stop rather than spin: logs survive for post-mortem, and any
                # committed work rides along on a rescue branch.
                print(f"{task} BLOCKED after {attempt} attempts; stopping for triage.")
                return EXIT_BLOCKED

        _remove_worktree(config.repo, wt)
        retire_branch(config.repo, branch, base_sha, task, attempt, published=bool(published))  # local ref only
        kill_task_processes(layout, task)  # belt-and-braces: nothing survives a task boundary
        if once:
            break

    drained = next_ready(config, layout.mirror, only_task=only_task) is None
    layout.write_heartbeat("-", 0, "exit", "QUEUE_DRAINED" if drained else "ONCE")
    return 0


def cmd_kill(config: BurnConfig) -> int:
    layout = config.layout
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.kill_file.write_text(now() + "\n")  # sentinel first, so a racing driver cannot start the next task
    kill_all(layout)
    layout.write_heartbeat("-", 0, "kill", "KILLED")
    print("killed. worktrees left intact for post-mortem; `resume` to continue.")
    return 0


def cmd_resume(config: BurnConfig, backend: Backend, integration) -> int:
    layout = config.layout
    layout.kill_file.unlink(missing_ok=True)
    reclaim_worktrees(config.repo, layout.worktrees, base_ref(config), load_attempts(layout))
    return cmd_run(config, backend, integration)


def cmd_status(config: BurnConfig) -> int:
    layout = config.layout
    ensure_mirror(config)
    counts: dict[str, int] = {}
    tasks = load_tasks(layout.mirror, config.tasks_dir, config.dep_dirs)
    for t in tasks.values():
        if is_phase_parent(tasks, t.id):
            continue  # phase parents don't count toward leaf progress
        counts[t.status] = counts.get(t.status, 0) + 1
    print("backlog:", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("next ready:", next_ready(config, layout.mirror) or "none (queue drained or stalled on the skip list)")
    # Gated on the consumer having declared a fallback, for the same reason
    # backend.planner is: an inert sentinel must not read as an active override.
    if config.fallback_model and (override := read_fallback_planner(layout)) is not None:
        print("planner override:", "|".join(override))
    # Through has_budget, not the raw count: a task revised since it failed is
    # already pickable again, and reporting it as blocked would send a human
    # to triage work the queue has moved on from.
    attempts = load_attempts(layout)
    blocked = {
        t.id: attempts[t.id].n for t in tasks.values() if t.id in attempts and not has_budget(t, attempts, config.max_attempts)
    }
    if blocked:
        print(
            "blocked (exhausted attempts; revise the task to clear):", ", ".join(f"{k}({v})" for k, v in sorted(blocked.items()))
        )
    print("kill sentinel:", "PRESENT" if layout.kill_file.exists() else "absent")
    if layout.heartbeat.exists():
        print("last heartbeats:")
        for line in layout.heartbeat.read_text().splitlines()[-5:]:
            print(" ", line)
    return 0


def main(
    config: BurnConfig,
    integration=None,
    backends: dict[str, Backend] | None = None,
    argv: list[str] | None = None,
) -> int:
    integration = integration or default_integration(config)
    backends = backends or default_backends(config)
    args = build_arg_parser(backends, config.default_backend).parse_args(argv)
    match args.cmd:
        case "run":
            backend = resolve_backend(backends, args.backend, config.default_backend)
            return cmd_run(config, backend, integration, once=args.once, only_task=args.task)
        case "resume":
            backend = resolve_backend(backends, args.backend, config.default_backend)
            return cmd_resume(config, backend, integration)
        case "status":
            return cmd_status(config)
        case "kill":
            return cmd_kill(config)
    return 1


def run_from_cli(config: BurnConfig, integration=None, backends: dict[str, Backend] | None = None) -> None:
    sys.exit(main(config, integration, backends))
