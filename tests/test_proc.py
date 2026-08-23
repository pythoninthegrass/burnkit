"""Process launch and termination.

The no-`pkill` test is the regression that motivated extracting this library:
the predecessor driver killed every matching process on the box, including an
unrelated session a human was running by hand. Termination must be scoped to
what this driver itself launched.
"""

import pytest
import signal
from burnkit import proc
from burnkit.config import BurnConfig
from burnkit.state import BurnLayout
from pathlib import Path

PROC_SOURCE = Path(proc.__file__).read_text()


def test_no_pkill_anywhere_in_proc() -> None:
    """A pattern match cannot distinguish this driver's agent from an unrelated
    one with the same command line. There is no safe scope for it here."""
    assert "pkill" not in PROC_SOURCE
    assert "killall" not in PROC_SOURCE


def test_kill_task_processes_signals_the_recorded_process_group(config: BurnConfig, monkeypatch) -> None:
    layout = config.layout
    layout.ensure_dirs()
    (layout.pids / "WK-000.01").write_text("4242\n")

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(proc.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(proc.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(proc, "herdr_available", lambda: False)

    proc.kill_task_processes(layout, "WK-000.01")

    assert killed == [(4243, signal.SIGKILL)]
    assert not (layout.pids / "WK-000.01").exists()


def test_kill_task_processes_tolerates_a_dead_pid(config: BurnConfig, monkeypatch) -> None:
    layout = config.layout
    layout.ensure_dirs()
    (layout.pids / "WK-000.01").write_text("4242\n")

    def boom(pid: int) -> int:
        raise ProcessLookupError

    monkeypatch.setattr(proc.os, "getpgid", boom)
    monkeypatch.setattr(proc, "herdr_available", lambda: False)

    proc.kill_task_processes(layout, "WK-000.01")  # must not raise
    assert not (layout.pids / "WK-000.01").exists()


def test_kill_task_processes_is_a_noop_without_a_pidfile(config: BurnConfig, monkeypatch) -> None:
    layout = config.layout
    layout.ensure_dirs()
    monkeypatch.setattr(proc.os, "killpg", lambda pgid, sig: pytest.fail("killed something unrecorded"))
    monkeypatch.setattr(proc, "herdr_available", lambda: False)
    proc.kill_task_processes(layout, "WK-000.01")


def test_kill_all_only_touches_pids_this_driver_recorded(config: BurnConfig, monkeypatch) -> None:
    layout = config.layout
    layout.ensure_dirs()
    (layout.pids / "WK-000.01").write_text("11\n")
    (layout.pids / "driver").write_text("22\n")

    killed: list[int] = []
    monkeypatch.setattr(proc.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(proc.os, "killpg", lambda pgid, sig: killed.append(pgid))
    monkeypatch.setattr(proc, "herdr_available", lambda: False)

    proc.kill_all(layout)

    assert sorted(killed) == [11, 22]


def test_wait_for_exit_returns_on_the_exit_marker(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("working\n" + proc.EXIT_MARKER + "0 ===\n")
    finished, _ = proc.wait_for_exit(log, kill_file=tmp_path / "KILL", timeout_s=1, poll_s=1)
    assert finished


def test_wait_for_exit_aborts_on_the_kill_sentinel(tmp_path: Path) -> None:
    kill = tmp_path / "KILL"
    kill.write_text("now\n")
    finished, _ = proc.wait_for_exit(tmp_path / "run.log", kill_file=kill, timeout_s=1, poll_s=1)
    assert not finished


def test_wait_for_exit_times_out_without_a_marker(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text("still going\n")
    finished, elapsed = proc.wait_for_exit(log, kill_file=tmp_path / "KILL", timeout_s=0, poll_s=1)
    assert not finished
    assert elapsed >= 0


def test_wait_for_exit_only_scans_the_tail(tmp_path: Path) -> None:
    """A marker echoed early in a long log (e.g. quoted back by the agent) is
    not an exit; only the launch wrapper's trailing marker counts."""
    log = tmp_path / "run.log"
    log.write_text(proc.EXIT_MARKER + "0 ===\n" + "x" * 5000)
    finished, _ = proc.wait_for_exit(log, kill_file=tmp_path / "KILL", timeout_s=0, poll_s=1)
    assert not finished


def test_sh_captures_output_as_text() -> None:
    cp = proc.sh("printf", "hello")
    assert cp.stdout == "hello"


def test_git_runs_in_the_requested_directory(tmp_path: Path) -> None:
    proc.sh("git", "init", "-q", "-b", "main", cwd=tmp_path)
    out = proc.git("symbolic-ref", "--short", "HEAD", cwd=tmp_path).stdout.strip()
    assert out == "main"


def test_launch_env_path_prepends_the_shim_directories() -> None:
    """Launched shells are non-login and non-interactive, so the runtime
    manager's shims are not on PATH unless put there explicitly."""
    env = proc.launch_env(secrets_env=Path("/nonexistent/.env"))
    assert env["PATH"].startswith(f"{Path.home()}/.local/share/mise/shims:")


def test_launch_env_reads_secrets_when_the_file_exists(tmp_path: Path) -> None:
    secrets = tmp_path / ".env"
    secrets.write_text("MODEL_API_KEY=from-file\n")
    env = proc.launch_env(secrets_env=secrets, secrets={"MODEL_API_KEY": "fallback"})
    assert env["MODEL_API_KEY"] == "from-file"


def test_launch_env_falls_back_to_the_declared_default(tmp_path: Path) -> None:
    env = proc.launch_env(secrets_env=tmp_path / "absent.env", secrets={"MODEL_API_KEY": "fallback"})
    assert env["MODEL_API_KEY"] == "fallback"
    assert "PATH" in env


def test_pidfile_path_is_under_the_run_layout(tmp_path: Path) -> None:
    layout = BurnLayout(tmp_path / "burn")
    assert proc.pidfile(layout, "WK-000.01") == tmp_path / "burn" / "pids" / "WK-000.01"
