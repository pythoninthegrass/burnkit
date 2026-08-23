"""The finish-marker protocol and prompt composition.

The markers are defined once, here, and both the prose the agent reads and the
parser that scans its log are derived from those definitions. In the driver
this was extracted from they were maintained separately — prose in a text file,
format strings in the parser — so a typo in either silently broke the
completion gate with no test to catch it.

Section ownership. A consumer owns its role line, its hard rules, its
verification instructions, and its documentation pointer; those are hand-tuned
against real runs and burnkit does not touch them. burnkit owns the four
sections below, because they describe the protocol between the agent and this
library rather than anything about the project.
"""

import re
from burnkit.backend import Backend
from burnkit.config import BurnConfig
from pathlib import Path

TASK_ID_PLACEHOLDER = "<TASK-ID>"
REASON_PLACEHOLDER = "<one line>"

DONE_TEMPLATE = "=== TASK:DONE {task} ==="
BAIL_TEMPLATE = "=== TASK:BAIL {task} reason={reason} ==="

# Alphanumeric so re.escape() passes it through untouched, letting the parser's
# capture group be spliced into the escaped form of the same template the agent
# was shown.
_REASON_SENTINEL = "BURNKITREASONCAPTURE"

TASK_FILE_SEPARATOR = "=== TASK FILE ==="


def done_marker(task: str) -> str:
    return DONE_TEMPLATE.format(task=task)


def bail_marker(task: str, reason: str) -> str:
    return BAIL_TEMPLATE.format(task=task, reason=reason)


def _bail_pattern(task: str) -> re.Pattern[str]:
    literal = re.escape(BAIL_TEMPLATE.format(task=task, reason=_REASON_SENTINEL))
    return re.compile(literal.replace(_REASON_SENTINEL, "(.*?)"))


def parse_marker(text: str, task: str) -> tuple[bool, str]:
    """Read an agent's log for its final marker. Returns (finished, reason).

    A bail is checked first: an agent that printed DONE and then bailed has not
    finished, and the reverse ordering would report success.
    """
    if f"=== TASK:BAIL {task}" in text:
        m = _bail_pattern(task).search(text)
        return False, m.group(1) if m else "unknown"
    if done_marker(task) not in text:
        return False, "no DONE marker"
    return True, ""


def finish_section(task_id: str = TASK_ID_PLACEHOLDER) -> str:
    return (
        "FINISH: kill any background processes you started, then print exactly one final line:\n"
        f"`{done_marker(task_id)}` or `{bail_marker(task_id, REASON_PLACEHOLDER)}`"
    )


def task_status_section() -> str:
    return (
        "TASK STATUS:\n"
        "- If every Acceptance Criterion and Definition-of-Done item is genuinely satisfied: check each "
        f"one off and set status to Done via `backlog task edit {TASK_ID_PLACEHOLDER} --check-ac 1 "
        "--check-ac 2 ... --check-dod 1 ... -s Done` (adjust indices to match the task file). Never "
        "hand-edit the YAML frontmatter directly — use the `backlog` CLI so the file format stays "
        "consistent.\n"
        '- If the task file has a "## Supervisor fix request" section, address the newest one first.\n'
        "- Never move, archive, or rewrite a task file's Description or Acceptance Criteria wording — "
        "only check off items and add Notes/Comments via the CLI."
    )


def git_section(author: str) -> str:
    """The driver owns publication. An agent that pushes or switches branches
    escapes the gates that run between its commit and the push."""
    return (
        f"GIT: commit your work with Conventional Commits style, author {author}, NO co-author trailers. "
        "Commit locally only — do NOT push, do NOT create or switch branches, do NOT amend or force "
        "anything. If you make a mess you can't unwind, `git reset --hard` before bailing."
    )


def bail_section(extra_conditions: tuple[str, ...] = ()) -> str:
    conditions = [
        "a missing tool or asset you cannot install",
        "a task that requires physical hardware or another human's judgment call",
        "an acceptance criterion you cannot verify after reasonable effort",
        "gates or checks still failing after 3 distinct fix attempts",
        "a real dependency on unfinished work the task file didn't declare",
        *extra_conditions,
    ]
    return (
        f"BAIL CONDITIONS: {', or '.join([', '.join(conditions[:-1]), conditions[-1]])}. When bailing, "
        "`git reset --hard` any uncommitted mess first — do not leave the task file half-edited."
    )


def protocol_sections(config: BurnConfig) -> str:
    finish = finish_section()
    if config.example_task_id:
        finish += f"\n(substituting the real task id, e.g. {config.example_task_id})."
    return "\n\n".join([task_status_section(), git_section(config.author), bail_section(config.extra_bail_conditions), finish])


def _read_if_present(path: Path | None) -> str:
    return path.read_text().strip() if path is not None and path.exists() else ""


def compose(config: BurnConfig, backend: Backend) -> str:
    """Project prose, then the protocol, then the backend's own note."""
    parts = [
        _read_if_present(config.prompt_project_fragment),
        protocol_sections(config),
        config.context7_line,
        _read_if_present(backend.prompt_fragment),
    ]
    return "\n\n".join(p for p in parts if p) + "\n"


def build_prompt(config: BurnConfig, backend: Backend, task_file: Path) -> str:
    return compose(config, backend) + f"\n{TASK_FILE_SEPARATOR}\n" + task_file.read_text()
