"""The proof-of-done gates.

Two gating tracks, both preserved from the driver this was extracted from: a
prose/decision task clears the marker+commit gate and the agent is trusted to
satisfy its own acceptance criteria; a code task additionally must clear
machine gates the driver itself ran. That distinction is what the trust classes
name — see test_integration.py.

Every function here is pure and takes its policy as an argument. None of them
read module state.
"""

from burnkit.config import MachineGate
from burnkit.gates import (
    TRUST_AGENT_ATTESTED,
    TRUST_MEASURED_LOCAL,
    GateReport,
    GateResult,
    Verdict,
    is_code_change,
    parse_acceptance_criteria,
    run_machine_gates,
    scope_violations,
    select_gates,
    unchecked_acceptance_criteria,
)
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

    def run(cmd, cwd, env=None):
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


def test_gate_env_is_threaded_to_the_runner(gates: tuple[MachineGate, ...]) -> None:
    seen: list[dict | None] = []

    def run(cmd, cwd, env=None):
        seen.append(env)
        return FakeProc(0)

    run_machine_gates(gates[:1], Path("/wt"), env={"PATH": "/shims"}, run=run)
    assert seen == [{"PATH": "/shims"}]


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


# --- verdict --------------------------------------------------------------


def test_a_verdict_defaults_to_agent_attested() -> None:
    """Nothing is measured until burnkit itself watched a gate pass."""
    assert Verdict(True, "1 commits").trust == TRUST_AGENT_ATTESTED


def test_a_gated_verdict_carries_its_report() -> None:
    report = GateReport([GateResult("build", True, "built")])
    verdict = Verdict(True, "gates green", report=report, trust=TRUST_MEASURED_LOCAL)
    assert verdict.report is report
    assert verdict.trust == TRUST_MEASURED_LOCAL
