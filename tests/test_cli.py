"""main()/run_from_cli() default `integration` to the fast-forward strategy
rather than requiring a consumer to choose. cmd_run's full task loop is
exercised by real overnight runs, not unit tests here -- this only covers the
one thing that changed: what a consumer gets by not passing `integration`.
"""

from burnkit import cli
from burnkit.backend import Backend
from burnkit.config import BurnConfig
from burnkit.integration import FastForwardBranch
from pathlib import Path


def test_main_defaults_integration_to_the_fast_forward_strategy(config: BurnConfig, monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(cli, "cmd_status", lambda c: seen.setdefault("config", c) and 0)
    monkeypatch.setattr(
        cli,
        "default_integration",
        lambda c: (seen.setdefault("integration_resolved_from", c), FastForwardBranch(c))[1],
    )

    cli.main(config, None, backends={}, argv=["status"])

    assert seen["integration_resolved_from"] is config


class TestStallWatch:
    """The stall hook is small enough to hide a bug and only runs during a real
    overnight loop, so it is unit-tested away from that loop."""

    def _backend(self, check):
        return Backend(
            name="b",
            remote_planner=False,
            shell_cmd="true",
            task_env=lambda t, p, log: {},
            prepare_worktree=lambda t, wt: None,
            launch_line="",
            prompt_fragment=Path("frag.txt"),
            stall_check=check,
        )

    def test_a_backend_without_a_stall_check_disables_the_hook(self, tmp_path: Path) -> None:
        # None, not a no-op callable: wait_for_exit must keep its old behavior.
        check, reasons = cli.stall_watch(self._backend(None), "WK-001", tmp_path, 0.0)
        assert check is None
        assert reasons == []

    def test_a_clear_run_reports_no_stall(self, tmp_path: Path) -> None:
        check, reasons = cli.stall_watch(self._backend(lambda t, wt, since: None), "WK-001", tmp_path, 0.0)
        assert check() is False
        assert reasons == []

    def test_a_stalled_run_trips_and_records_why(self, tmp_path: Path) -> None:
        check, reasons = cli.stall_watch(self._backend(lambda t, wt, since: "no progress: looping"), "WK-001", tmp_path, 0.0)
        assert check() is True
        assert reasons == ["no progress: looping"]

    def test_the_check_is_asked_about_this_task_worktree_and_launch_time(self, tmp_path: Path) -> None:
        seen = []
        check, _ = cli.stall_watch(self._backend(lambda t, wt, since: seen.append((t, wt, since))), "WK-001", tmp_path, 123.0)
        check()
        assert seen == [("WK-001", tmp_path, 123.0)]
