"""verify(): the whole gate, in the order the gates actually run.

Order is load-bearing and each step exists because a real run got past the
previous one:

  bail marker  ->  done marker  ->  at least one new commit  ->  every
  acceptance criterion checked  ->  (code tasks) diff within scope  ->
  (code tasks) machine gates green

An agent that hits its turn cap gets forced into a summary turn, where it can
print DONE and *describe* bookkeeping it never actually performed. Every step
after the marker exists to catch a variant of that.
"""

import dataclasses
import pytest
from burnkit import prompt
from burnkit.config import BurnConfig
from burnkit.gates import TRUST_AGENT_ATTESTED, TRUST_MEASURED_LOCAL, verify
from burnkit.proc import git, sh
from pathlib import Path

TASK = "WK-009.42"
ALL_CHECKED = "## Acceptance Criteria\n\n- [x] #1 Do the thing\n"
ONE_UNCHECKED = "## Acceptance Criteria\n\n- [x] #1 Do the thing\n- [ ] #2 Do the other thing\n"


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def wt(tmp_path: Path, config: BurnConfig) -> Path:
    """A worktree-shaped git repo with one base commit and a task file."""
    w = tmp_path / "wt"
    (w / config.tasks_dir).mkdir(parents=True)
    sh("git", "init", "-q", "-b", "main", cwd=w)
    sh("git", "config", "user.email", "test@test", cwd=w)
    sh("git", "config", "user.name", "test", cwd=w)
    (w / "README.md").write_text("base\n")
    sh("git", "add", "README.md", cwd=w)
    sh("git", "commit", "-q", "-m", "base", cwd=w)
    return w


@pytest.fixture
def base_sha(wt: Path) -> str:
    return git("rev-parse", "HEAD", cwd=wt).stdout.strip()


def write_task_file(wt: Path, config: BurnConfig, body: str = ALL_CHECKED, status: str = "Done") -> Path:
    f = wt / config.tasks_dir / f"{TASK.lower()} - some task.md"
    f.write_text(f"---\nid: {TASK}\ntitle: some task\nstatus: {status}\ndependencies: []\nordinal: 1\n---\n\n{body}")
    return f


def commit(wt: Path, rel: str, text: str = "work\n") -> None:
    p = wt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-q", "-m", f"feat: {rel}", cwd=wt)


def log_with(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "run.log"
    f.write_text(text)
    return f


def noop_backlog_done(task: str, wt: Path) -> None:
    """The real one shells out to the backlog CLI; these tests are hermetic."""
    return None


def verify_it(config: BurnConfig, wt: Path, log: Path, base_sha: str, run=None):
    return verify(config, TASK, wt, log, base_sha, run=run, ensure_done=noop_backlog_done)


# --- marker gate ----------------------------------------------------------


def test_a_bail_marker_fails_with_its_reason(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    log = log_with(tmp_path, prompt.bail_marker(TASK, "missing tool"))
    verdict = verify_it(config, wt, log, base_sha)
    assert not verdict.ok
    assert "missing tool" in verdict.reason


def test_a_missing_marker_fails(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    verdict = verify_it(config, wt, log_with(tmp_path, "the agent rambled"), base_sha)
    assert not verdict.ok
    assert "DONE marker" in verdict.reason


def test_a_missing_log_fails_rather_than_raising(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    verdict = verify_it(config, wt, tmp_path / "never-written.log", base_sha)
    assert not verdict.ok


# --- commit gate ----------------------------------------------------------


def test_a_done_marker_without_commits_fails(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    """The agent said it finished but left nothing behind."""
    verdict = verify_it(config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha)
    assert not verdict.ok
    assert "no new commits" in verdict.reason


# --- acceptance-criteria gate --------------------------------------------


def test_an_unchecked_criterion_blocks_a_done_marker(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    write_task_file(wt, config, body=ONE_UNCHECKED)
    commit(wt, "docs/notes.md")
    verdict = verify_it(config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha)
    assert not verdict.ok
    assert "#2" in verdict.reason


def test_a_prose_task_with_everything_checked_passes_as_attested(
    config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path
) -> None:
    """No machine gate ran, so the evidence is the agent's own word."""
    write_task_file(wt, config)
    commit(wt, "docs/notes.md")
    verdict = verify_it(config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha)
    assert verdict.ok
    assert verdict.trust == TRUST_AGENT_ATTESTED
    assert verdict.report is None


# --- scope gate -----------------------------------------------------------


def test_a_code_task_straying_outside_its_scope_fails(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    write_task_file(wt, config)
    commit(wt, "src/clamp.c")
    commit(wt, "unrelated/module.py")
    verdict = verify_it(config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha)
    assert not verdict.ok
    assert "unrelated/module.py" in verdict.reason


def test_a_scope_violation_is_measured_not_attested(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    """burnkit read the diff itself, so this verdict is its own measurement."""
    write_task_file(wt, config)
    commit(wt, "src/clamp.c")
    commit(wt, "unrelated/module.py")
    verdict = verify_it(config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha)
    assert verdict.trust == TRUST_MEASURED_LOCAL


# --- machine gates --------------------------------------------------------


def test_a_code_task_must_clear_the_machine_gates(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    write_task_file(wt, config)
    commit(wt, "src/clamp.c")
    calls: list[list[str]] = []

    def run(cmd, cwd, env=None, **_):
        calls.append(list(cmd))
        return FakeProc(1, "", "linker error")

    verdict = verify_it(config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha, run=run)
    assert not verdict.ok
    assert "machine gate" in verdict.reason
    assert verdict.trust == TRUST_MEASURED_LOCAL
    assert calls == [["task", "build"]]


def test_a_green_code_task_is_measured_local(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    write_task_file(wt, config)
    commit(wt, "src/clamp.c")
    verdict = verify_it(
        config, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha, run=lambda cmd, cwd, env=None, **_: FakeProc(0, "ok")
    )
    assert verdict.ok
    assert verdict.trust == TRUST_MEASURED_LOCAL
    assert verdict.report is not None
    assert verdict.report.ok


def test_a_prose_task_never_runs_a_machine_gate(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    write_task_file(wt, config)
    commit(wt, "docs/notes.md")
    verify_it(
        config,
        wt,
        log_with(tmp_path, prompt.done_marker(TASK)),
        base_sha,
        run=lambda cmd, cwd, env=None, **_: pytest.fail("ran a gate for a non-code task"),
    )


def test_no_phase_parent_is_closed_out_unless_the_consumer_asked(
    config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path
) -> None:
    """A consumer publishing one pull request per task wants the parent left to
    a human; only a shared-branch consumer wants it swept up automatically."""
    write_task_file(wt, config)
    commit(wt, "docs/notes.md")
    verify(
        config,
        TASK,
        wt,
        log_with(tmp_path, prompt.done_marker(TASK)),
        base_sha,
        ensure_done=noop_backlog_done,
        close_out=lambda task, w: pytest.fail("closed out a phase parent without being asked"),
    )


def test_the_phase_parent_closeout_runs_before_publication(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    """Before, not after: the parent's own Done commit then rides the published
    branch instead of being written into the consumer's checkout behind its back."""
    cfg = dataclasses.replace(config, close_out_phase_parents=True)
    write_task_file(wt, cfg)
    commit(wt, "docs/notes.md")
    closed: list[str] = []
    verdict = verify(
        cfg,
        TASK,
        wt,
        log_with(tmp_path, prompt.done_marker(TASK)),
        base_sha,
        ensure_done=noop_backlog_done,
        close_out=lambda task, w: closed.append(task),
    )
    assert verdict.ok
    assert closed == [TASK]


def test_a_failing_task_closes_out_nothing(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    cfg = dataclasses.replace(config, close_out_phase_parents=True)
    write_task_file(wt, cfg, body=ONE_UNCHECKED)
    commit(wt, "docs/notes.md")
    verify(
        cfg,
        TASK,
        wt,
        log_with(tmp_path, prompt.done_marker(TASK)),
        base_sha,
        ensure_done=noop_backlog_done,
        close_out=lambda task, w: pytest.fail("closed out a phase parent for a failed task"),
    )


def test_the_backlog_status_fixup_runs_before_publication(config: BurnConfig, wt: Path, base_sha: str, tmp_path: Path) -> None:
    """A turn-capped agent can describe having set its status to Done without
    the call executing; the queue reads that status, so a stale To Do gets the
    task handed straight back out."""
    write_task_file(wt, config, status="To Do")
    commit(wt, "docs/notes.md")
    fixed: list[str] = []
    verdict = verify(
        config, TASK, wt, log_with(tmp_path, prompt.done_marker(TASK)), base_sha, ensure_done=lambda task, w: fixed.append(task)
    )
    assert verdict.ok
    assert fixed == [TASK]
