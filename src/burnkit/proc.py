"""Subprocess helpers and scoped process termination.

Termination in this module is always scoped to a recorded pid, never to a
command-line pattern match — a pattern cannot distinguish an agent this driver
launched from one a human is running by hand in another window, and the
predecessor driver's machine-wide pattern kill did in fact take out an
unrelated session mid-run. Every launched shell records its own pid into
`$BURN_PIDFILE` before doing anything else, and termination goes through that
pid's process group.

test_proc.py asserts mechanically that no pattern-matching kill utility is
named anywhere in this file, which is why this note avoids naming one too.
"""

import contextlib
import os
import signal
import subprocess
import time
from burnkit.state import BurnLayout
from collections.abc import Callable
from decouple import Config, RepositoryEnv
from pathlib import Path

EXIT_MARKER = "=== AGENT_EXIT:"
# Only the tail of a log is scanned for the marker: an agent that quotes the
# marker back mid-run has not exited, and the launch wrapper always appends the
# real one last.
_LOG_TAIL_BYTES = 2000


def sh(
    *args: str, cwd: Path | None = None, check: bool = True, timeout: int = 300, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=check, timeout=timeout, env=env, capture_output=True, text=True)


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return sh("git", *args, cwd=cwd, check=check)


def backlog(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return sh("backlog", *args, cwd=cwd, check=check)


def launch_env(secrets_env: Path | None = None, secrets: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a launched agent shell.

    The runtime manager's shims are prepended to PATH because launched shells
    are neither login nor interactive, so nothing else puts them there and the
    agent's own tool invocations would fail to resolve.
    """
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/share/mise/shims:{Path.home()}/.local/bin:" + env.get("PATH", "")
    if not secrets:
        return env
    source = Config(RepositoryEnv(str(secrets_env))) if secrets_env and secrets_env.exists() else None
    for key, default in secrets.items():
        env[key] = source(key, default=default) if source is not None else default
    return env


def herdr_available() -> bool:
    try:
        return subprocess.run(["herdr", "agent", "list"], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pidfile(layout: BurnLayout, task: str) -> Path:
    return layout.pids / task


def launch(task: str, wt: Path, shell_cmd: str, task_env: dict, env: dict, forward: tuple[str, ...] = ()) -> None:
    """Prefer a monitored agent pane; fall back to a direct child in its own
    process group so termination can still reach the whole tree.

    `forward` names keys of `env` to pass through to the pane: a pane does not
    inherit this process's environment, so a resolved secret reaches the agent
    only by being listed here. The fallback child inherits `env` wholesale and
    needs no such list.
    """
    if herdr_available():
        cmd = ["herdr", "agent", "start", task, "--cwd", str(wt), "--no-focus"]
        forwarded = {k: env.get(k, "") for k in forward}
        for k, v in {**task_env, **forwarded, "PATH": env["PATH"]}.items():
            cmd += ["--env", f"{k}={v}"]
        cmd += ["--", "bash", "-c", shell_cmd]
        if subprocess.run(cmd, check=False, timeout=60).returncode == 0:
            return
    subprocess.Popen(["bash", "-c", shell_cmd], cwd=wt, env={**env, **task_env}, start_new_session=True)


def _kill_recorded_pid(path: Path) -> None:
    with contextlib.suppress(ProcessLookupError, ValueError, PermissionError, OSError):
        pid = int(path.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGKILL)


def kill_task_processes(layout: BurnLayout, task: str) -> None:
    """Kill exactly what this driver launched for `task` — the process group the
    launched shell recorded its own pid into."""
    if herdr_available():
        subprocess.run(["herdr", "pane", "close", task], check=False, capture_output=True, timeout=15)
    path = pidfile(layout, task)
    if path.exists():
        _kill_recorded_pid(path)
        path.unlink(missing_ok=True)


def kill_all(layout: BurnLayout) -> None:
    """Emergency stop, scoped to every pid this driver recorded — the launched
    task shells and the driver's own."""
    if layout.pids.exists():
        for path in sorted(layout.pids.iterdir()):
            _kill_recorded_pid(path)
    if herdr_available():
        subprocess.run(["herdr", "pane", "close", "driver"], check=False, capture_output=True, timeout=15)


def wait_for_exit(
    log: Path,
    *,
    kill_file: Path,
    timeout_s: int,
    poll_s: int,
    stall_check: Callable[[], bool] | None = None,
) -> tuple[bool, float]:
    """Block until the launch wrapper appends its exit marker, the kill sentinel
    appears, `stall_check` reports no progress, or the timeout expires. Returns
    (finished, elapsed_seconds).

    `stall_check` is optional because not every backend leaves a transcript to
    judge progress from. It is checked after the exit marker, so a run that
    looped and then finished still counts as finished.
    """
    start = time.monotonic()
    while True:
        if kill_file.exists():
            return False, time.monotonic() - start
        if log.exists() and EXIT_MARKER in log.read_text(errors="replace")[-_LOG_TAIL_BYTES:]:
            return True, time.monotonic() - start
        if stall_check is not None and stall_check():
            return False, time.monotonic() - start
        if time.monotonic() - start >= timeout_s:
            return False, time.monotonic() - start
        time.sleep(poll_s)
