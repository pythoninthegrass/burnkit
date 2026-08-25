"""main()/run_from_cli() default `integration` to the fast-forward strategy
rather than requiring a consumer to choose. cmd_run's full task loop is
exercised by real overnight runs, not unit tests here -- this only covers the
one thing that changed: what a consumer gets by not passing `integration`.
"""

from burnkit import cli
from burnkit.backend import Backend
from burnkit.config import BurnConfig
from burnkit.integration import FastForwardBranch
from burnkit.proc import git, sh
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


class TestReclaimWorktrees:
    """`kill` SIGKILLs the driver mid-loop, so the loop tail that would have
    retired the attempt branch never runs. `git worktree add -b` then refuses
    to recreate that branch, and `resume` died on the first task it tried --
    exactly the task the kill interrupted."""

    @staticmethod
    def _repo(tmp_path: Path) -> tuple[Path, Path, str]:
        repo = tmp_path / "repo"
        repo.mkdir()
        sh("git", "init", "-q", "-b", "main", cwd=repo)
        sh("git", "config", "user.email", "test@test", cwd=repo)
        sh("git", "config", "user.name", "test", cwd=repo)
        (repo / "f.txt").write_text("base\n")
        sh("git", "add", "f.txt", cwd=repo)
        sh("git", "commit", "-q", "-m", "base", cwd=repo)
        base_sha = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
        return repo, tmp_path / "wt", base_sha

    def test_a_killed_attempt_frees_its_branch_for_relaunch(self, tmp_path: Path) -> None:
        repo, worktrees, _ = self._repo(tmp_path)
        wt = worktrees / "WK-009.42"
        sh("git", "worktree", "add", "-q", "-b", "agent/wk-009.42", str(wt), "main", cwd=repo)

        cli.reclaim_worktrees(repo, worktrees, "main", {})

        assert not wt.exists()
        assert git("rev-parse", "--verify", "agent/wk-009.42", cwd=repo, check=False).returncode != 0
        # the whole point: cmd_run can cut the branch again
        sh("git", "worktree", "add", "-q", "-b", "agent/wk-009.42", str(wt), "main", cwd=repo)

    def test_commits_a_killed_attempt_made_are_rescued_not_dropped(self, tmp_path: Path) -> None:
        """A kill can land after the agent committed real work. Only the reflog
        survives `branch -D`, and that is not enough to find it later."""
        repo, worktrees, _ = self._repo(tmp_path)
        wt = worktrees / "WK-009.42"
        sh("git", "worktree", "add", "-q", "-b", "agent/wk-009.42", str(wt), "main", cwd=repo)
        (wt / "f.txt").write_text("base\nwork\n")
        sh("git", "commit", "-q", "-am", "work", cwd=wt)
        work_sha = git("rev-parse", "HEAD", cwd=wt).stdout.strip()

        cli.reclaim_worktrees(repo, worktrees, "main", {"WK-009.42": 2})

        assert git("rev-parse", "rescue/WK-009.42.a2", cwd=repo).stdout.strip() == work_sha

    def test_a_branch_that_only_lagged_behind_main_is_not_rescued(self, tmp_path: Path) -> None:
        """The attempt branched off an older tip and committed nothing. Judging
        it against the *current* tip would mint a rescue ref per killed task,
        each pointing at a commit already on main."""
        repo, worktrees, _ = self._repo(tmp_path)
        wt = worktrees / "WK-009.42"
        sh("git", "worktree", "add", "-q", "-b", "agent/wk-009.42", str(wt), "main", cwd=repo)
        (repo / "f.txt").write_text("base\nmoved on\n")
        sh("git", "commit", "-q", "-am", "main moved on", cwd=repo)

        cli.reclaim_worktrees(repo, worktrees, "main", {"WK-009.42": 1})

        assert git("rev-parse", "--verify", "rescue/WK-009.42.a1", cwd=repo, check=False).returncode != 0

    def test_worktrees_outside_the_burn_dir_are_left_alone(self, tmp_path: Path) -> None:
        """A consumer's own worktrees share the repo; reclaiming is scoped to
        the ones burnkit created."""
        repo, worktrees, _ = self._repo(tmp_path)
        mine = tmp_path / "my-own-worktree"
        sh("git", "worktree", "add", "-q", "-b", "my-feature", str(mine), "main", cwd=repo)

        cli.reclaim_worktrees(repo, worktrees, "main", {})

        assert mine.exists()
        assert git("rev-parse", "--verify", "my-feature", cwd=repo, check=False).returncode == 0

    def test_no_leftovers_is_not_an_error(self, tmp_path: Path) -> None:
        repo, worktrees, _ = self._repo(tmp_path)
        cli.reclaim_worktrees(repo, worktrees, "main", {})  # must not raise
