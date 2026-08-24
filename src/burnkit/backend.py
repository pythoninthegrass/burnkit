"""Launch backends.

A Backend is policy flags plus callables — deliberately not a class hierarchy,
because the two that exist differ in four small ways and nothing else in the
library should branch on a backend name.

`remote_planner` is the only flag with teeth. It says whether the agent's
planning step leaves this machine, which is what a content boundary is actually
coupled to; a fully-local backend is not asked to satisfy a rule that exists to
keep content away from a third party.
"""

import shutil
import subprocess
import time
import urllib.request
import zstandard
from burnkit import dshlog
from burnkit.config import BurnConfig
from burnkit.proc import EXIT_MARKER
from burnkit.state import read_fallback_planner
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HERMES_CMD = (
    'set -o pipefail; echo $$ > "$BURN_PIDFILE"; '
    'hermes chat -q "$(cat "$BURN_PROMPT")" --model "$BURN_MODEL" --provider "$BURN_PROVIDER" '
    '--yolo -Q --max-turns "$BURN_MAX_TURNS" --accept-hooks 2>&1 | tee "$BURN_LOG"; '
    f'echo "{EXIT_MARKER}$? ===" >> "$BURN_LOG"'
)

# DSH_PERMISSION_MODE=danger-full-access (set in the task env, not here):
# required because a task always runs inside a git worktree, whose .git
# metadata lives outside the worktree directory and trips the default
# workspace-write sandbox's git-write checks.
DSH_CMD = (
    'set -o pipefail; echo $$ > "$BURN_PIDFILE"; '
    'if [ -f "$BURN_DSH_ENV" ]; then set -a; source "$BURN_DSH_ENV"; set +a; fi; '
    'dsh --profile headless "$(cat "$BURN_PROMPT")" 2>&1 | tee "$BURN_LOG"; '
    f'echo "{EXIT_MARKER}$? ===" >> "$BURN_LOG"'
)


@dataclass(frozen=True)
class Backend:
    name: str
    remote_planner: bool
    shell_cmd: str
    task_env: Callable[[str, Path, Path], dict[str, str]]
    prepare_worktree: Callable[[str, Path], None]
    launch_line: str
    prompt_fragment: Path
    # Given (task, worktree, attempt launch time), why the run looks stuck, or
    # None. Optional because only a backend that leaves a readable transcript
    # can answer it.
    stall_check: Callable[[str, Path, float], str | None] | None = None


def _noop_prepare(task: str, wt: Path) -> None:
    """Leave the worktree exactly as checked out."""
    return None


def copy_prepare(repo: Path, rels: tuple[str, ...]) -> Callable[[str, Path], None]:
    """Build a prepare_worktree that copies out-of-tree files into the worktree.

    A copy rather than a link because a gate may run a tool that writes to the
    file, and that must not reach back into the consumer's own checkout. Use
    symlink_prepare for content that is only ever read.
    """

    def prepare(task: str, wt: Path) -> None:
        for rel in rels:
            src = repo / rel
            dst = wt / rel
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    return prepare


def symlink_prepare(repo: Path, rels: tuple[str, ...]) -> Callable[[str, Path], None]:
    """Build a prepare_worktree that links out-of-tree content into the worktree,
    so a local agent reads it like any other file instead of through env roots.

    Only appropriate for a backend with no remote planner.
    """

    def prepare(task: str, wt: Path) -> None:
        for rel in rels:
            src = repo / rel
            dst = wt / rel
            if src.is_dir() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.symlink_to(src, target_is_directory=True)

    return prepare


def planner(config: BurnConfig) -> tuple[str, str]:
    """The (model, provider) the next launch plans with.

    Read per launch rather than once at startup, so a run that demotes its
    planner mid-queue launches the very next task on the demoted one. Demotion
    is strictly opt-in: with no fallback declared, a stale sentinel left behind
    by another consumer cannot silently redirect this one.
    """
    if config.fallback_model and (override := read_fallback_planner(config.layout)) is not None:
        return override
    return config.model, config.provider


def hermes_backend(
    config: BurnConfig,
    *,
    prepare_worktree: Callable[[str, Path], None] | None = None,
    launch_line: str | None = None,
) -> Backend:
    def task_env(task: str, prompt_file: Path, log: Path) -> dict[str, str]:
        model, provider = planner(config)
        return {
            "BURN_PROMPT": str(prompt_file),
            "BURN_LOG": str(log),
            "BURN_PIDFILE": str(config.layout.pids / task),
            "BURN_MODEL": model,
            "BURN_PROVIDER": provider,
            "BURN_MAX_TURNS": str(config.max_turns),
            **config.launch_env(task, "hermes"),
        }

    default_line = (
        f"Planning: `{config.model}` via {config.provider} (remote). "
        f"Building: `{config.builder_model}` via {config.builder_provider} (local)."
    )
    return Backend(
        name="hermes",
        remote_planner=True,
        shell_cmd=HERMES_CMD,
        task_env=task_env,
        prepare_worktree=prepare_worktree or _noop_prepare,
        launch_line=launch_line or default_line,
        prompt_fragment=config.backend_fragment("hermes"),
    )


def dsh_backend(
    config: BurnConfig,
    *,
    prepare_worktree: Callable[[str, Path], None] | None = None,
    launch_line: str | None = None,
    sessions_root: Path = dshlog.SESSIONS_ROOT,
) -> Backend:
    def stall_check(task: str, wt: Path, since: float) -> str | None:
        """dsh writes a full transcript, so progress can be judged from what the
        agent actually ran. An absent or unreadable session log yields None:
        not-yet-written is not evidence of a stall."""
        log = dshlog.find_session_log(wt, sessions_root=sessions_root, since=since)
        if log is None:
            return None
        try:
            return dshlog.stall_reason(dshlog.tool_call_signatures(log))
        except (OSError, ValueError, zstandard.ZstdError):
            return None  # mid-write truncation; the next poll reads a whole record

    def task_env(task: str, prompt_file: Path, log: Path) -> dict[str, str]:
        return {
            "BURN_PROMPT": str(prompt_file),
            "BURN_LOG": str(log),
            "BURN_PIDFILE": str(config.layout.pids / task),
            "BURN_DSH_ENV": str(config.dsh_env_file),
            "DSH_PERMISSION_MODE": "danger-full-access",
            **config.launch_env(task, "dsh"),
        }

    default_line = (
        "Fully local: `dsh --profile headless` drives a locally-served model directly against this "
        "worktree. Nothing about this run leaves the machine."
    )
    return Backend(
        name="dsh",
        remote_planner=False,
        shell_cmd=DSH_CMD,
        task_env=task_env,
        prepare_worktree=prepare_worktree or _noop_prepare,
        launch_line=launch_line or default_line,
        prompt_fragment=config.backend_fragment("dsh"),
        stall_check=stall_check,
    )


def resolve_backend(backends: dict[str, Backend], name: str | None, default: str) -> Backend:
    key = (name or default).strip().lower()
    if key not in backends:
        raise ValueError(f"unknown backend: {key!r} (choices: {', '.join(sorted(backends))})")
    return backends[key]


def preflight_hooks_for(config: BurnConfig, backend: Backend, wt: Path) -> list[str]:
    """Run the consumer's content checks that apply to this backend, returning
    every offender. A non-empty result must abort the launch."""
    offenders: list[str] = []
    for hook in config.preflight_hooks:
        if hook.remote_planner_only and not backend.remote_planner:
            continue
        offenders.extend(hook.check(wt))
    return offenders


def preflight_local_model(url: str, *, restart_container: str | None = None) -> bool:
    """Check the local model server, optionally restarting a named container
    once before giving up. Pass no restart_container for a plain reachability
    check with no side effect."""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        if attempt == 0 and restart_container:
            subprocess.run(["docker", "restart", restart_container], check=False, timeout=120)
            time.sleep(90)
    return False
