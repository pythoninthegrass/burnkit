"""The proof-of-done gates.

Two gating tracks, both preserved from the driver this was extracted from: a
prose/decision task clears the marker+commit gate and the agent is trusted to
satisfy its own acceptance criteria; a code task additionally must clear
machine gates the driver itself ran. That distinction is what the trust classes
name — see test_integration.py.

Every function here is pure and takes its policy as an argument. None of them
read module state.
"""

import os
import time
from burnkit.config import MachineGate
from burnkit.gates import (
    DEFAULT_GATE_TIMEOUT_S,
    TRUST_AGENT_ATTESTED,
    TRUST_MEASURED_LOCAL,
    GateReport,
    GateResult,
    Verdict,
    is_code_change,
    parse_acceptance_criteria,
    phase_parent_to_close,
    run_machine_gates,
    scope_violations,
    select_gates,
    unchecked_acceptance_criteria,
)
from burnkit.queue import Task
from pathlib import Path

CODE_PREFIXES = ("src/", "include/", "tests/", "specs/")
ALLOWED_PREFIXES = CODE_PREFIXES + ("backlog/", "docs/")


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def recording_runner(results: list[FakeProc]):
    calls: list[list[str]] = []

    def run(cmd, cwd, env=None, **_):
        calls.append(list(cmd))
        return results[len(calls) - 1]

    run.calls = calls
    return run


# --- code-change selector -------------------------------------------------


def test_a_source_path_is_a_code_change() -> None:
    assert is_code_change(["src/clamp.c"], CODE_PREFIXES)


def test_header_and_test_paths_are_code_changes() -> None:
    assert is_code_change(["include/rng.h"], CODE_PREFIXES)
    assert is_code_change(["tests/test_clamp.c"], CODE_PREFIXES)


def test_a_spec_path_is_a_code_change() -> None:
    assert is_code_change(["specs/clamp.json"], CODE_PREFIXES)


def test_docs_and_backlog_paths_are_not_code_changes() -> None:
    assert not is_code_change(["docs/reference.md", "backlog/tasks/wk-009.08 - x.md"], CODE_PREFIXES)


def test_an_empty_diff_is_not_a_code_change() -> None:
    assert not is_code_change([], CODE_PREFIXES)


# --- machine gates --------------------------------------------------------


def test_all_green_reports_ok_and_runs_every_gate(gates: tuple[MachineGate, ...]) -> None:
    run = recording_runner([FakeProc(0, "built"), FakeProc(0, "12 cases, 0 failures")])
    report = run_machine_gates(gates, Path("/wt"), run=run)
    assert report.ok
    assert len(report.results) == 2
    assert run.calls == [["task", "build"], ["task", "test"]]


def test_gates_stop_at_the_first_failure(gates: tuple[MachineGate, ...]) -> None:
    run = recording_runner([FakeProc(1, "", "test_foo FAILED"), FakeProc(0)])
    report = run_machine_gates(gates, Path("/wt"), run=run)
    assert not report.ok
    assert len(run.calls) == 1
    assert "FAIL" in report.evidence_summary()
    assert "FAILED" in report.results[0].evidence


def test_evidence_summary_names_every_gate_and_its_outcome(gates: tuple[MachineGate, ...]) -> None:
    run = recording_runner([FakeProc(0, "built"), FakeProc(0, "ok")])
    summary = run_machine_gates(gates, Path("/wt"), run=run).evidence_summary()
    assert "build" in summary
    assert "unit" in summary
    assert "pass" in summary


def test_an_empty_gate_list_is_not_a_pass() -> None:
    """A "zero gates ran" result must never read as evidence — that is the
    shape of a vacuous pass."""
    assert not GateReport().ok


def test_gate_evidence_is_truncated_but_keeps_the_tail(gates: tuple[MachineGate, ...]) -> None:
    run = recording_runner([FakeProc(1, "noise\n" * 2000 + "the real error")])
    report = run_machine_gates(gates, Path("/wt"), run=run)
    assert "the real error" in report.results[0].evidence
    assert len(report.results[0].evidence) < 2000


# --- vacuous gates --------------------------------------------------------
#
# A gate whose suite is empty exits 0 having checked nothing. That is a green
# light for work nobody verified, so `vacuous_if` lets the consumer declare the
# gate's own "nothing to verify" signature and burnkit stops counting it.

NOTHING_TO_VERIFY = "no specs found -- nothing to verify yet, not evidence of correctness"
VACUOUS_GATE = MachineGate(
    name="diff-verify",
    argv=("task", "diff-verify"),
    vacuous_if=lambda out: "nothing to verify" in out,
)


def test_a_gate_reporting_nothing_to_verify_is_marked_vacuous() -> None:
    run = recording_runner([FakeProc(0, NOTHING_TO_VERIFY)])
    report = run_machine_gates((VACUOUS_GATE,), Path("/wt"), run=run)
    assert report.results[0].vacuous


def test_a_gate_that_actually_checked_something_is_not_vacuous() -> None:
    run = recording_runner([FakeProc(0, "12 specs, 0 failures")])
    report = run_machine_gates((VACUOUS_GATE,), Path("/wt"), run=run)
    assert not report.results[0].vacuous


def test_an_all_vacuous_report_is_not_a_pass() -> None:
    """Same reasoning as the zero-gate case: nothing was checked, so there is
    nothing to read as evidence."""
    run = recording_runner([FakeProc(0, NOTHING_TO_VERIFY)])
    report = run_machine_gates((VACUOUS_GATE,), Path("/wt"), run=run)
    assert not report.ok
    assert not report.failed


def test_one_real_gate_carries_a_report_that_also_holds_a_vacuous_one() -> None:
    run = recording_runner([FakeProc(0, "built"), FakeProc(0, NOTHING_TO_VERIFY)])
    report = run_machine_gates((MachineGate(name="build", argv=("task", "build")), VACUOUS_GATE), Path("/wt"), run=run)
    assert report.ok


def test_a_failing_gate_is_never_vacuous() -> None:
    """A non-zero exit is a failure even if the output matches the consumer's
    vacuity signature -- otherwise the predicate could mask a real break."""
    run = recording_runner([FakeProc(1, NOTHING_TO_VERIFY)])
    report = run_machine_gates((VACUOUS_GATE,), Path("/wt"), run=run)
    assert not report.results[0].vacuous
    assert report.failed


def test_evidence_summary_says_vacuous_rather_than_pass() -> None:
    """review-queue.md is read as a record of what was verified; a vacuous gate
    logged as `pass` makes it lie."""
    run = recording_runner([FakeProc(0, NOTHING_TO_VERIFY)])
    summary = run_machine_gates((VACUOUS_GATE,), Path("/wt"), run=run).evidence_summary()
    assert "vacuous" in summary
    assert "pass" not in summary


def test_a_gate_without_a_vacuity_predicate_is_never_vacuous(gates: tuple[MachineGate, ...]) -> None:
    run = recording_runner([FakeProc(0, NOTHING_TO_VERIFY), FakeProc(0, "ok")])
    report = run_machine_gates(gates, Path("/wt"), run=run)
    assert not any(r.vacuous for r in report.results)
    assert report.ok


def test_gate_env_is_threaded_to_the_runner(gates: tuple[MachineGate, ...]) -> None:
    seen: list[dict | None] = []

    def run(cmd, cwd, env=None, **_):
        seen.append(env)
        return FakeProc(0)

    run_machine_gates(gates[:1], Path("/wt"), env={"PATH": "/shims"}, run=run)
    assert seen == [{"PATH": "/shims"}]


def test_a_gate_without_its_own_timeout_gets_the_default(gates: tuple[MachineGate, ...]) -> None:
    seen: list[int] = []

    def run(cmd, cwd, env=None, timeout_s=None):
        seen.append(timeout_s)
        return FakeProc(0)

    run_machine_gates(gates[:1], Path("/wt"), run=run)
    assert seen == [DEFAULT_GATE_TIMEOUT_S]


def test_a_gates_own_timeout_reaches_the_runner() -> None:
    """Gate durations differ by orders of magnitude — a lint pass and a
    full replay run cannot share one ceiling without either killing the slow
    gate or letting a hung fast one hold the whole queue."""
    seen: list[int] = []

    def run(cmd, cwd, env=None, timeout_s=None):
        seen.append(timeout_s)
        return FakeProc(0)

    tuned = (
        MachineGate("lint", ("task", "lint"), timeout_s=180),
        MachineGate("replay", ("task", "replay"), timeout_s=600),
    )
    run_machine_gates(tuned, Path("/wt"), run=run)
    assert seen == [180, 600]


def test_select_gates_keeps_unconditional_gates() -> None:
    """applies=None means always — this is what a consumer running a fixed gate
    list registers."""
    fixed = (MachineGate("build", ("task", "build")), MachineGate("lint", ("task", "lint")))
    assert select_gates(fixed, ["anything"]) == fixed


def test_select_gates_applies_the_predicate_to_changed_paths() -> None:
    conditional = (
        MachineGate("build", ("task", "build")),
        MachineGate("spec", ("task", "spec"), applies=lambda paths: any(p.startswith("specs/") for p in paths)),
    )
    assert [g.name for g in select_gates(conditional, ["src/x.c"])] == ["build"]
    assert [g.name for g in select_gates(conditional, ["specs/x.json"])] == ["build", "spec"]


# --- acceptance-criteria gate --------------------------------------------


def test_all_checked_criteria_leave_nothing_unchecked() -> None:
    text = "## Acceptance Criteria\n\n- [x] #1 Do the thing\n- [x] #2 Do another\n\n## Notes\nx\n"
    assert unchecked_acceptance_criteria(text) == []


def test_an_unchecked_criterion_is_reported() -> None:
    """An agent that hits its turn cap can print a DONE marker without ever
    checking its own boxes."""
    text = "## Acceptance Criteria\n\n- [x] #1 Done\n- [ ] #2 Not done\n"
    assert unchecked_acceptance_criteria(text) == ["#2"]


def test_a_task_without_the_section_imposes_no_gate() -> None:
    assert unchecked_acceptance_criteria("## Description\nbody\n") == []


def test_the_section_stops_at_the_next_heading() -> None:
    text = "## Acceptance Criteria\n\n- [ ] #1 Not done\n\n## Implementation Plan\n\n- [x] #99 unrelated\n"
    assert unchecked_acceptance_criteria(text) == ["#1"]


def test_parse_acceptance_criteria_returns_index_text_and_state() -> None:
    text = "## Acceptance Criteria\n\n- [x] #1 First thing\n- [ ] #2 Second thing\n"
    assert parse_acceptance_criteria(text) == [(1, "First thing", True), (2, "Second thing", False)]


def test_an_uppercase_check_marker_counts_as_checked() -> None:
    assert unchecked_acceptance_criteria("## Acceptance Criteria\n\n- [X] #1 Done\n") == []


# --- scope gate -----------------------------------------------------------


def test_paths_within_the_allowlist_are_clean() -> None:
    paths = ["src/clamp.c", "specs/clamp.json", "backlog/tasks/wk-009.42 - x.md", "docs/reference.md"]
    assert scope_violations(paths, ALLOWED_PREFIXES) == []


def test_a_path_outside_the_allowlist_is_flagged() -> None:
    paths = ["src/clamp.c", "unrelated/module.py"]
    assert scope_violations(paths, ALLOWED_PREFIXES) == ["unrelated/module.py"]


def test_an_empty_diff_has_no_scope_violations() -> None:
    assert scope_violations([], ALLOWED_PREFIXES) == []


# --- gate timeouts --------------------------------------------------------


def wait_until_gone(pid: int, deadline_s: float = 5.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            return True
        time.sleep(0.05)
    return False


def test_a_timed_out_gate_fails_instead_of_ending_the_run(tmp_path: Path) -> None:
    """One slow gate should fail its task, not raise out of verify() and take
    the whole overnight queue with it."""
    gate = MachineGate("hang", ("bash", "-c", "sleep 30"), timeout_s=1)
    report = run_machine_gates((gate,), tmp_path)
    assert not report.ok
    assert "timed out" in report.results[0].evidence


def test_a_timed_out_gate_takes_its_whole_process_tree_with_it(tmp_path: Path) -> None:
    """A gate that hangs is usually hanging inside helpers it spawned; killing
    only the direct child leaves those running to interfere with the next gate.
    Scoped to the gate's own process group — never to a process name."""
    marker = tmp_path / "child.pid"
    gate = MachineGate("hang", ("bash", "-c", f"sleep 30 & echo $! > {marker}; wait"), timeout_s=1)
    run_machine_gates((gate,), tmp_path)
    assert wait_until_gone(int(marker.read_text().strip()))


# --- phase-parent closeout ------------------------------------------------


def leaf(tid: str, status: str, parent: str) -> Task:
    return Task(id=tid, status=status, title=tid, parent_task_id=parent)


def test_the_last_done_sibling_closes_out_its_phase_parent() -> None:
    """A phase parent is bookkeeping over its leaves, so nothing but the leaves
    can tell you when it is finished."""
    tasks = {
        "WK-009": Task(id="WK-009", status="To Do", title="phase"),
        "WK-009.01": leaf("WK-009.01", "Done", "WK-009"),
        "WK-009.02": leaf("WK-009.02", "Done", "WK-009"),
    }
    assert phase_parent_to_close(tasks, "WK-009.02") == "WK-009"


def test_an_open_sibling_leaves_the_phase_parent_alone() -> None:
    tasks = {
        "WK-009": Task(id="WK-009", status="To Do", title="phase"),
        "WK-009.01": leaf("WK-009.01", "Done", "WK-009"),
        "WK-009.02": leaf("WK-009.02", "To Do", "WK-009"),
    }
    assert phase_parent_to_close(tasks, "WK-009.01") is None


def test_a_leaf_without_a_parent_closes_nothing() -> None:
    tasks = {"WK-009.01": Task(id="WK-009.01", status="Done", title="orphan leaf")}
    assert phase_parent_to_close(tasks, "WK-009.01") is None


def test_an_already_done_parent_is_not_closed_again() -> None:
    tasks = {
        "WK-009": Task(id="WK-009", status="Done", title="phase"),
        "WK-009.01": leaf("WK-009.01", "Done", "WK-009"),
    }
    assert phase_parent_to_close(tasks, "WK-009.01") is None


def test_an_unknown_task_closes_nothing() -> None:
    assert phase_parent_to_close({}, "WK-009.01") is None


# --- verdict --------------------------------------------------------------


def test_a_verdict_defaults_to_agent_attested() -> None:
    """Nothing is measured until burnkit itself watched a gate pass."""
    assert Verdict(True, "1 commits").trust == TRUST_AGENT_ATTESTED


def test_a_gated_verdict_carries_its_report() -> None:
    report = GateReport([GateResult("build", True, "built")])
    verdict = Verdict(True, "gates green", report=report, trust=TRUST_MEASURED_LOCAL)
    assert verdict.report is report
    assert verdict.trust == TRUST_MEASURED_LOCAL
