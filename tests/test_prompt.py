"""Prompt composition and the finish-marker protocol.

In the driver this was extracted from, the marker text existed twice — as prose
in a prompt-header file the agent reads, and as format strings in the parser
that scans the log for it — in two different file types with no test tying them
together. A typo in either silently broke the completion gate. Here the prose
is generated from the same constants the parser uses, and the tests below are
what keeps that true.
"""

import dataclasses
from burnkit import prompt
from burnkit.backend import dsh_backend, hermes_backend
from burnkit.config import BurnConfig

TASK = "WK-009.42"


# --- single-sourcing ------------------------------------------------------


def test_the_generated_finish_prose_contains_the_exact_done_marker() -> None:
    section = prompt.finish_section(prompt.TASK_ID_PLACEHOLDER)
    assert prompt.done_marker(prompt.TASK_ID_PLACEHOLDER) in section


def test_the_generated_finish_prose_is_matched_by_the_bail_parser() -> None:
    """Substituting a real id and reason into the prose the agent was shown must
    produce a line the parser actually matches."""
    section = prompt.finish_section(prompt.TASK_ID_PLACEHOLDER)
    line = section.replace(prompt.TASK_ID_PLACEHOLDER, TASK).replace(prompt.REASON_PLACEHOLDER, "gate stayed red")
    assert prompt.parse_marker(line, TASK) == (False, "gate stayed red")


def test_the_done_marker_is_recognized() -> None:
    log = f"...agent output...\n{prompt.done_marker(TASK)}\n"
    assert prompt.parse_marker(log, TASK) == (True, "")


def test_a_bail_marker_wins_over_a_done_marker() -> None:
    """An agent that bails after having printed DONE earlier has not finished."""
    log = prompt.done_marker(TASK) + "\n" + prompt.bail_marker(TASK, "missing tool")
    assert prompt.parse_marker(log, TASK) == (False, "missing tool")


def test_a_bail_without_a_parseable_reason_still_bails() -> None:
    assert prompt.parse_marker(f"=== TASK:BAIL {TASK} ===", TASK) == (False, "unknown")


def test_no_marker_at_all_is_not_a_done() -> None:
    assert prompt.parse_marker("the agent said some things", TASK) == (False, "no DONE marker")


def test_another_tasks_marker_does_not_satisfy_this_task() -> None:
    assert prompt.parse_marker(prompt.done_marker("WK-000.01"), TASK)[0] is False


def test_the_reason_capture_stops_at_the_marker_terminator() -> None:
    log = prompt.bail_marker(TASK, "one line") + "\ntrailing chatter === more ===\n"
    assert prompt.parse_marker(log, TASK)[1] == "one line"


# --- protocol sections ----------------------------------------------------


def test_the_protocol_carries_the_task_status_git_bail_and_finish_sections(config: BurnConfig) -> None:
    protocol = prompt.protocol_sections(config)
    for heading in ("TASK STATUS:", "GIT:", "BAIL CONDITIONS:", "FINISH:"):
        assert heading in protocol


def test_the_git_section_names_the_configured_author(config: BurnConfig) -> None:
    assert f"author {config.author}" in prompt.protocol_sections(config)


def test_the_git_section_forbids_publishing_from_inside_the_agent(config: BurnConfig) -> None:
    """The driver owns publication; an agent that pushes or switches branches
    breaks the gate that runs between its commit and the push."""
    git_section = prompt.protocol_sections(config)
    assert "do NOT push" in git_section
    assert "NO co-author trailers" in git_section


def test_the_bail_section_states_the_repeated_failure_rule(config: BurnConfig) -> None:
    assert "3 distinct fix attempts" in prompt.protocol_sections(config)


def test_extra_bail_conditions_are_appended(config: BurnConfig) -> None:
    cfg = dataclasses.replace(config, extra_bail_conditions=("a widget that needs recalibrating",))
    assert "a widget that needs recalibrating" in prompt.protocol_sections(cfg)


def test_the_finish_section_uses_the_configured_example_id(config: BurnConfig) -> None:
    assert config.example_task_id in prompt.protocol_sections(config)


def test_the_task_status_section_forbids_hand_editing_frontmatter(config: BurnConfig) -> None:
    assert "Never hand-edit the YAML frontmatter" in prompt.protocol_sections(config)


# --- composition ----------------------------------------------------------


def test_composition_places_the_project_fragment_before_the_protocol(config: BurnConfig) -> None:
    config.prompt_project_fragment.write_text("ROLE LINE\n\nHARD RULES:\n- be careful\n")
    backend = dsh_backend(config)
    backend.prompt_fragment.write_text("BACKEND NOTE\n")

    composed = prompt.compose(config, backend)

    assert composed.index("ROLE LINE") < composed.index("TASK STATUS:")
    assert composed.index("TASK STATUS:") < composed.index("BACKEND NOTE")


def test_composition_includes_the_context7_line(config: BurnConfig) -> None:
    config.prompt_project_fragment.write_text("ROLE\n")
    backend = dsh_backend(config)
    backend.prompt_fragment.write_text("BACKEND\n")
    assert config.context7_line in prompt.compose(config, backend)


def test_every_backend_composition_carries_the_shared_invariants(config: BurnConfig) -> None:
    """Drift guard for the per-backend split: whichever fragment is appended,
    the finish markers, the author rule, and the no-push rule must survive."""
    config.prompt_project_fragment.write_text("ROLE\n")
    for factory in (dsh_backend, hermes_backend):
        backend = factory(config)
        backend.prompt_fragment.write_text(f"{backend.name} note\n")
        composed = prompt.compose(config, backend)
        assert "TASK:DONE" in composed
        assert "TASK:BAIL" in composed
        assert config.author in composed
        assert "do NOT push" in composed
        assert f"{backend.name} note" in composed


def test_composition_omits_a_missing_backend_fragment(config: BurnConfig) -> None:
    """A consumer with nothing backend-specific to say should not need to ship
    an empty file."""
    config.prompt_project_fragment.write_text("ROLE\n")
    backend = dsh_backend(config)
    assert not backend.prompt_fragment.exists()
    assert "TASK STATUS:" in prompt.compose(config, backend)


def test_build_prompt_appends_the_task_file(config: BurnConfig, tmp_path) -> None:
    config.prompt_project_fragment.write_text("ROLE\n")
    backend = dsh_backend(config)
    backend.prompt_fragment.write_text("BACKEND\n")
    task_file = tmp_path / "task.md"
    task_file.write_text("## Acceptance Criteria\n\n- [ ] #1 do it\n")

    full = prompt.build_prompt(config, backend, task_file)

    assert full.index("TASK STATUS:") < full.index("#1 do it")
    assert "=== TASK FILE ===" in full


def test_the_reason_placeholder_appears_once_for_the_agent_to_fill_in(config: BurnConfig) -> None:
    assert prompt.protocol_sections(config).count(prompt.REASON_PLACEHOLDER) == 1
