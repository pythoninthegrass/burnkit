"""Backend selection and launch.

A Backend is policy flags plus callables — no class hierarchy. Everything else
in the library reads those fields instead of branching on a backend name.

`remote_planner` is the load-bearing flag: it says whether the agent's planning
step leaves this machine. Preflight hooks that exist to keep local content off
a third party are gated on it, so a fully-local backend is not asked to satisfy
a boundary that only applies to a remote one.
"""

import dataclasses
import json
import pytest
from burnkit.backend import (
    DSH_CMD,
    HERMES_CMD,
    Backend,
    copy_prepare,
    dsh_backend,
    hermes_backend,
    planner,
    resolve_backend,
    symlink_prepare,
)
from burnkit.cli import build_arg_parser
from burnkit.config import BurnConfig
from burnkit.proc import EXIT_MARKER
from burnkit.state import record_fallback_planner
from pathlib import Path


@pytest.fixture
def backends(config: BurnConfig) -> dict[str, Backend]:
    return {"dsh": dsh_backend(config), "hermes": hermes_backend(config)}


def test_backend_names(backends: dict[str, Backend]) -> None:
    assert backends["dsh"].name == "dsh"
    assert backends["hermes"].name == "hermes"


def test_default_backend_is_dsh(backends: dict[str, Backend], config: BurnConfig) -> None:
    assert resolve_backend(backends, None, config.default_backend) is backends["dsh"]


def test_an_explicit_backend_is_selectable(backends: dict[str, Backend], config: BurnConfig) -> None:
    assert resolve_backend(backends, "hermes", config.default_backend) is backends["hermes"]


def test_backend_selection_is_case_and_whitespace_tolerant(backends: dict[str, Backend]) -> None:
    assert resolve_backend(backends, " Hermes ", "dsh") is backends["hermes"]


def test_an_unknown_backend_raises(backends: dict[str, Backend]) -> None:
    with pytest.raises(ValueError):
        resolve_backend(backends, "bogus", "dsh")


def test_remote_planner_differs_by_backend(backends: dict[str, Backend]) -> None:
    """hermes plans in the cloud; dsh drives a locally-served model."""
    assert backends["hermes"].remote_planner
    assert not backends["dsh"].remote_planner


def test_the_exit_marker_is_backend_agnostic() -> None:
    assert EXIT_MARKER == "=== AGENT_EXIT:"
    assert EXIT_MARKER in HERMES_CMD
    assert EXIT_MARKER in DSH_CMD


def test_launch_commands_record_their_own_pid_before_running() -> None:
    """This is what makes scoped termination possible at all — see
    test_proc.py's no-pkill rule."""
    assert 'echo $$ > "$BURN_PIDFILE"' in HERMES_CMD
    assert 'echo $$ > "$BURN_PIDFILE"' in DSH_CMD


def test_launch_commands_fail_on_a_broken_pipe() -> None:
    """Both commands pipe through tee; without pipefail the wrapper reports
    tee's exit status instead of the agent's."""
    assert HERMES_CMD.startswith("set -o pipefail;")
    assert DSH_CMD.startswith("set -o pipefail;")


def test_dsh_command_shape() -> None:
    assert "dsh --profile headless" in DSH_CMD
    assert "BURN_DSH_ENV" in DSH_CMD


def test_dsh_task_env_sets_permission_mode_and_env_file(config: BurnConfig) -> None:
    """danger-full-access is required because a task always runs inside a git
    worktree, whose .git metadata lives outside the worktree directory and
    trips the default sandbox's git-write checks."""
    backend = dsh_backend(config)
    env = backend.task_env("WK-000.00", Path("/prompts/x.txt"), Path("/logs/x.log"))
    assert env["DSH_PERMISSION_MODE"] == "danger-full-access"
    assert env["BURN_DSH_ENV"] == str(config.dsh_env_file)
    assert env["BURN_PROMPT"] == "/prompts/x.txt"
    assert env["BURN_LOG"] == "/logs/x.log"
    assert env["BURN_PIDFILE"] == str(config.layout.pids / "WK-000.00")


def test_hermes_task_env_carries_the_model_selection(config: BurnConfig) -> None:
    backend = hermes_backend(config)
    env = backend.task_env("WK-000.00", Path("/prompts/x.txt"), Path("/logs/x.log"))
    assert env["BURN_MODEL"] == config.model
    assert env["BURN_PROVIDER"] == config.provider
    assert env["BURN_MAX_TURNS"] == str(config.max_turns)


def test_hermes_task_env_includes_the_consumers_launch_env(config: BurnConfig) -> None:
    """A consumer hands out-of-tree roots to its local tools this way, so the
    worktree a remote planner reads stays free of them."""
    cfg = dataclasses.replace(config, launch_env=lambda task, backend: {"WIDGET_DATA_ROOT": f"/out/{task}"})
    env = hermes_backend(cfg).task_env("WK-000.00", Path("/p"), Path("/l"))
    assert env["WIDGET_DATA_ROOT"] == "/out/WK-000.00"


def test_launch_env_is_told_which_backend_it_is_building_for(config: BurnConfig) -> None:
    """Out-of-tree roots exist so a *remote*-planner backend never has the
    content in its worktree. A fully-local backend reads that content directly,
    so a consumer scopes the roots by backend instead of handing every backend
    environment it does not use.
    """
    cfg = dataclasses.replace(
        config,
        launch_env=lambda task, backend: {"WIDGET_DATA_ROOT": "/out"} if backend == "hermes" else {},
    )
    assert "WIDGET_DATA_ROOT" in hermes_backend(cfg).task_env("WK-000.00", Path("/p"), Path("/l"))
    assert "WIDGET_DATA_ROOT" not in dsh_backend(cfg).task_env("WK-000.00", Path("/p"), Path("/l"))


def test_symlink_prepare_links_the_requested_trees(tmp_path: Path) -> None:
    """A fully-local backend can read a consumer's out-of-tree content directly
    instead of going through env roots."""
    repo = tmp_path / "repo"
    (repo / "data" / "local").mkdir(parents=True)
    (repo / "data" / "local" / "x.bin").write_bytes(b"\x00")
    wt = tmp_path / "wt"
    wt.mkdir()

    symlink_prepare(repo, ("data/local",))("WK-000.01", wt)

    assert (wt / "data" / "local").is_symlink()
    assert (wt / "data" / "local" / "x.bin").exists()


def test_symlink_prepare_tolerates_a_missing_source(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    symlink_prepare(repo, ("data/local",))("WK-000.01", wt)  # must not raise
    assert not (wt / "data").exists()


def test_symlink_prepare_leaves_an_existing_path_alone(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    wt = tmp_path / "wt"
    (wt / "data").mkdir(parents=True)
    symlink_prepare(repo, ("data",))("WK-000.01", wt)
    assert not (wt / "data").is_symlink()


def test_copy_prepare_copies_the_requested_files(tmp_path: Path) -> None:
    """A copy, not a symlink: a gate may run a tool that writes to the file, and
    that must not reach back into the consumer's own checkout."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "fixture.bin").write_bytes(b"\x01\x02")
    wt = tmp_path / "wt"
    wt.mkdir()

    copy_prepare(repo, ("fixture.bin",))("WK-000.01", wt)

    assert (wt / "fixture.bin").read_bytes() == b"\x01\x02"
    assert not (wt / "fixture.bin").is_symlink()


def test_a_copied_file_is_isolated_from_the_original(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "fixture.bin").write_bytes(b"\x01")
    wt = tmp_path / "wt"
    wt.mkdir()
    copy_prepare(repo, ("fixture.bin",))("WK-000.01", wt)
    (wt / "fixture.bin").write_bytes(b"\xff")
    assert (repo / "fixture.bin").read_bytes() == b"\x01"


def test_copy_prepare_tolerates_a_missing_source(tmp_path: Path) -> None:
    """The file is an optional local artifact; a checkout without it still runs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    copy_prepare(repo, ("fixture.bin",))("WK-000.01", wt)  # must not raise
    assert not (wt / "fixture.bin").exists()


def test_copy_prepare_leaves_an_existing_file_alone(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "fixture.bin").write_bytes(b"\x01")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "fixture.bin").write_bytes(b"\xee")
    copy_prepare(repo, ("fixture.bin",))("WK-000.01", wt)
    assert (wt / "fixture.bin").read_bytes() == b"\xee"


# --- planner demotion -----------------------------------------------------


def test_the_planner_defaults_to_the_configured_model(config: BurnConfig) -> None:
    cfg = dataclasses.replace(config, model="remote-model", provider="remote-provider")
    assert planner(cfg) == ("remote-model", "remote-provider")


def test_a_recorded_fallback_takes_over_the_planner(config: BurnConfig) -> None:
    """Read per launch, not once at startup: a run that demotes its planner
    mid-queue must launch the next task on the demoted one."""
    cfg = dataclasses.replace(
        config,
        model="remote-model",
        provider="remote-provider",
        fallback_model="local-model",
        fallback_provider="local-provider",
    )
    record_fallback_planner(cfg.layout, "local-model", "local-provider")
    assert planner(cfg) == ("local-model", "local-provider")


def test_a_recorded_fallback_is_ignored_when_the_consumer_declared_none(config: BurnConfig) -> None:
    """Demotion is strictly opt-in, so a stale sentinel cannot silently redirect
    a consumer that never asked for a fallback."""
    cfg = dataclasses.replace(config, model="remote-model", provider="remote-provider")
    record_fallback_planner(cfg.layout, "local-model", "local-provider")
    assert planner(cfg) == ("remote-model", "remote-provider")


def test_hermes_launches_on_the_demoted_planner(config: BurnConfig) -> None:
    cfg = dataclasses.replace(
        config,
        model="remote-model",
        provider="remote-provider",
        fallback_model="local-model",
        fallback_provider="local-provider",
    )
    record_fallback_planner(cfg.layout, "local-model", "local-provider")
    env = hermes_backend(cfg).task_env("WK-000.00", Path("/p"), Path("/l"))
    assert env["BURN_MODEL"] == "local-model"
    assert env["BURN_PROVIDER"] == "local-provider"


def test_the_default_prepare_worktree_is_a_noop(config: BurnConfig, tmp_path: Path) -> None:
    """A backend with a remote planner has nothing to prepare — the worktree
    stays exactly as checked out."""
    wt = tmp_path / "empty-wt"
    wt.mkdir()
    hermes_backend(config).prepare_worktree("WK-000.01", wt)
    assert list(wt.iterdir()) == []


# --- argument parsing -----------------------------------------------------


def test_run_backend_flag_is_honored(backends: dict[str, Backend]) -> None:
    ns = build_arg_parser(backends, "dsh").parse_args(["run", "--backend", "hermes"])
    assert ns.backend == "hermes"


def test_run_backend_defaults_to_none_meaning_the_configured_default(backends: dict[str, Backend]) -> None:
    ns = build_arg_parser(backends, "dsh").parse_args(["run"])
    assert ns.backend is None
    assert resolve_backend(backends, ns.backend, "dsh") is backends["dsh"]


def test_an_invalid_backend_choice_is_rejected(backends: dict[str, Backend]) -> None:
    with pytest.raises(SystemExit):
        build_arg_parser(backends, "dsh").parse_args(["run", "--backend", "bogus"])


def test_resume_also_accepts_a_backend_flag(backends: dict[str, Backend]) -> None:
    ns = build_arg_parser(backends, "dsh").parse_args(["resume", "--backend", "dsh"])
    assert ns.backend == "dsh"


def test_run_accepts_once_and_task(backends: dict[str, Backend]) -> None:
    ns = build_arg_parser(backends, "dsh").parse_args(["run", "--once", "--task", "WK-009.42"])
    assert ns.once
    assert ns.task == "WK-009.42"


def test_a_subcommand_is_required(backends: dict[str, Backend]) -> None:
    with pytest.raises(SystemExit):
        build_arg_parser(backends, "dsh").parse_args([])


def test_status_and_kill_take_no_backend(backends: dict[str, Backend]) -> None:
    for cmd in ("status", "kill"):
        ns = build_arg_parser(backends, "dsh").parse_args([cmd])
        assert ns.cmd == cmd
        assert not hasattr(ns, "backend")


class TestStallChecks:
    """Only a backend that leaves a readable transcript can judge progress.
    hermes does not, so it declares none rather than guessing."""

    def test_dsh_declares_a_stall_check(self, config: BurnConfig) -> None:
        assert dsh_backend(config).stall_check is not None

    def test_hermes_declares_none(self, config: BurnConfig) -> None:
        assert hermes_backend(config).stall_check is None

    def test_a_run_with_no_session_log_is_not_called_stalled(self, config: BurnConfig, tmp_path: Path) -> None:
        # Absence of evidence is not evidence of a stall: a session that has
        # not written its log yet must not be killed for it.
        check = dsh_backend(config, sessions_root=tmp_path).stall_check
        assert check("WK-001", tmp_path / "never-used", 0.0) is None

    def test_a_looping_session_is_reported(self, config: BurnConfig, tmp_path: Path) -> None:
        wt = tmp_path / "wt" / "WK-001"
        d = tmp_path / "sessions" / "encoded" / "session-1"
        d.mkdir(parents=True)
        cycle = [f"render {p}" for p in "ABCDEFGH"]
        events = [{"type": "session", "id": "session-1", "cwd": str(wt)}]
        for cmd in cycle * 6:
            events.append({"type": "tool/call", "data": {"name": "bash", "arguments": json.dumps({"command": cmd})}})
        (d / "session.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        check = dsh_backend(config, sessions_root=tmp_path / "sessions").stall_check
        assert "no progress" in check("WK-001", wt, 0.0)
