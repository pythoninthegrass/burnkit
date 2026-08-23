"""Publication: how a passing attempt reaches a human, and how a failing one is
disposed of without destroying work.

Two strategies ship here because the two drivers this was extracted from differ
exactly at this point: one opens a pull request per task, the other
fast-forwards into a shared branch. Everything upstream of publication is
identical, which is why this is the only place the split lives.
"""

import dataclasses
import pytest
from burnkit.config import BurnConfig
from burnkit.gates import TRUST_AGENT_ATTESTED, TRUST_MEASURED_LOCAL, GateReport, GateResult
from burnkit.integration import FastForwardBranch, PullRequestPerTask, append_review_queue, pr_body, retire_branch
from burnkit.proc import git, sh
from pathlib import Path

TASK = "WK-009.42"


@pytest.fixture
def green_report() -> GateReport:
    return GateReport([GateResult("build", True, "built ok"), GateResult("unit", True, "12 cases, 0 failures")])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=r)
    sh("git", "config", "user.email", "test@test", cwd=r)
    sh("git", "config", "user.name", "test", cwd=r)
    (r / "README.md").write_text("base\n")
    sh("git", "add", "README.md", cwd=r)
    sh("git", "commit", "-q", "-m", "base", cwd=r)
    return r


@pytest.fixture
def base_sha(repo: Path) -> str:
    return git("rev-parse", "HEAD", cwd=repo).stdout.strip()


# --- PR body --------------------------------------------------------------


def test_pr_body_carries_per_gate_evidence(config: BurnConfig, green_report: GateReport) -> None:
    body = pr_body(config, TASK, green_report, trust=TRUST_MEASURED_LOCAL)
    assert TASK in body
    assert "build" in body
    assert "unit" in body
    assert "12 cases, 0 failures" in body


def test_pr_body_for_an_ungated_task_has_no_gate_block(config: BurnConfig) -> None:
    body = pr_body(config, "WK-002.18", None)
    assert "WK-002.18" in body
    assert "Machine gates" not in body


def test_a_measured_local_body_has_no_human_confirmation_banner(config: BurnConfig, green_report: GateReport) -> None:
    body = pr_body(config, TASK, green_report, trust=TRUST_MEASURED_LOCAL)
    assert TRUST_MEASURED_LOCAL in body
    assert "human confirmation" not in body.lower()


def test_an_agent_attested_body_carries_the_human_confirmation_banner(config: BurnConfig) -> None:
    """An agent's own report of its work is never satisfying on its own."""
    body = pr_body(config, "WK-002.18", None, trust=TRUST_AGENT_ATTESTED)
    assert TRUST_AGENT_ATTESTED in body
    assert "human confirmation" in body.lower()


def test_pr_body_defaults_to_the_untrusted_class(config: BurnConfig) -> None:
    assert TRUST_AGENT_ATTESTED in pr_body(config, "WK-002.18", None)


def test_pr_body_requests_the_configured_reviewer(config: BurnConfig) -> None:
    assert f"@{config.reviewer}" in pr_body(config, TASK, None)


def test_pr_body_includes_the_backend_launch_line(config: BurnConfig) -> None:
    body = pr_body(config, TASK, None, launch_line="ran under a locally-served model")
    assert "ran under a locally-served model" in body


def test_a_failed_gate_is_reported_as_such(config: BurnConfig) -> None:
    report = GateReport([GateResult("build", False, "linker error")])
    body = pr_body(config, TASK, report, trust=TRUST_MEASURED_LOCAL)
    assert "FAIL" in body
    assert "linker error" in body


# --- review queue ---------------------------------------------------------


def test_review_queue_appends_one_line_per_publication(tmp_path: Path) -> None:
    qf = tmp_path / "review-queue.md"
    append_review_queue(qf, TASK, "agent/wk-009-42-x", "gates: build pass, unit pass")
    append_review_queue(qf, "WK-009.43", "agent/wk-009-43-y", "gates: build pass, unit pass")
    lines = [ln for ln in qf.read_text().splitlines() if "WK-009" in ln]
    assert len(lines) == 2
    assert TASK in lines[0]
    assert "WK-009.43" in lines[1]


def test_a_review_queue_line_carries_its_trust_tag(tmp_path: Path) -> None:
    qf = tmp_path / "review-queue.md"
    append_review_queue(qf, "WK-002.18", "agent/x", "1 commits", trust=TRUST_AGENT_ATTESTED)
    assert f"[{TRUST_AGENT_ATTESTED}]" in qf.read_text().splitlines()[-1]


def test_the_review_queue_directory_is_created_on_demand(tmp_path: Path) -> None:
    qf = tmp_path / "nested" / "deeper" / "review-queue.md"
    append_review_queue(qf, TASK, "agent/x", "1 commits")
    assert qf.exists()


# --- branch retirement ----------------------------------------------------


def test_a_branch_with_no_new_commits_is_deleted(repo: Path, base_sha: str) -> None:
    sh("git", "branch", "agent/x", cwd=repo)
    retire_branch(repo, "agent/x", base_sha, TASK, 1)
    branches = git("branch", "--list", cwd=repo).stdout
    assert "agent/x" not in branches
    assert "rescue/" not in branches


def test_a_branch_with_committed_work_is_rescued(repo: Path, base_sha: str) -> None:
    """A failed attempt can still have produced real committed work before it
    hit a gate. Only the log survives a bare `git branch -D`, and that is not
    enough to recover it before the next `git gc`."""
    sh("git", "checkout", "-q", "-b", "agent/x", cwd=repo)
    (repo / "work.txt").write_text("real work\n")
    sh("git", "add", "work.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "attempt work", cwd=repo)
    sh("git", "checkout", "-q", "main", cwd=repo)

    retire_branch(repo, "agent/x", base_sha, TASK, 3)

    branches = git("branch", "--list", cwd=repo).stdout
    assert "agent/x" not in branches
    assert f"rescue/{TASK}.a3" in branches


def test_a_rescue_overwrites_a_stale_same_named_rescue_branch(repo: Path, base_sha: str) -> None:
    sh("git", "branch", f"rescue/{TASK}.a1", cwd=repo)
    sh("git", "checkout", "-q", "-b", "agent/y", cwd=repo)
    (repo / "work2.txt").write_text("more work\n")
    sh("git", "add", "work2.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "attempt work 2", cwd=repo)
    sh("git", "checkout", "-q", "main", cwd=repo)

    retire_branch(repo, "agent/y", base_sha, TASK, 1)

    branches = git("branch", "--list", cwd=repo).stdout
    assert "agent/y" not in branches
    assert f"rescue/{TASK}.a1" in branches
    assert git("rev-parse", f"rescue/{TASK}.a1", cwd=repo).stdout.strip() != base_sha


def test_retiring_a_branch_that_does_not_exist_is_harmless(repo: Path, base_sha: str) -> None:
    retire_branch(repo, "agent/never-created", base_sha, TASK, 1)


# --- integration strategies ----------------------------------------------


def test_pull_request_strategy_reports_its_own_name(config: BurnConfig) -> None:
    assert PullRequestPerTask(config).name == "pull-request-per-task"


def test_fast_forward_strategy_reports_its_own_name(config: BurnConfig) -> None:
    assert FastForwardBranch(config, branch="burn").name == "fast-forward-branch"


def test_fast_forward_merges_the_attempt_into_the_shared_branch(repo: Path, config: BurnConfig, base_sha: str) -> None:
    sh("git", "branch", "burn", cwd=repo)
    sh("git", "checkout", "-q", "-b", "agent/x", cwd=repo)
    (repo / "work.txt").write_text("real work\n")
    sh("git", "add", "work.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "attempt work", cwd=repo)
    attempt_sha = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    sh("git", "checkout", "-q", "main", cwd=repo)

    strategy = FastForwardBranch(dataclasses.replace(config, repo=repo), branch="burn", push=False)
    published = strategy.publish(TASK, "some task", "agent/x", repo, trust=TRUST_MEASURED_LOCAL, report=None)

    assert published
    assert git("rev-parse", "burn", cwd=repo).stdout.strip() == attempt_sha


def test_fast_forward_refuses_a_non_fast_forward(repo: Path, config: BurnConfig) -> None:
    """A shared branch that diverged means someone else published in between;
    rewriting it would discard their work."""
    sh("git", "checkout", "-q", "-b", "burn", cwd=repo)
    (repo / "other.txt").write_text("someone else\n")
    sh("git", "add", "other.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "other work", cwd=repo)
    sh("git", "checkout", "-q", "main", cwd=repo)
    sh("git", "checkout", "-q", "-b", "agent/x", cwd=repo)
    (repo / "work.txt").write_text("mine\n")
    sh("git", "add", "work.txt", cwd=repo)
    sh("git", "commit", "-q", "-m", "my work", cwd=repo)
    sh("git", "checkout", "-q", "main", cwd=repo)

    strategy = FastForwardBranch(dataclasses.replace(config, repo=repo), branch="burn", push=False)
    assert strategy.publish(TASK, "some task", "agent/x", repo, trust=TRUST_MEASURED_LOCAL, report=None) is None
