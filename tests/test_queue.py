"""The task queue: frontmatter parsing, the completed/archive precedence rule,
and next_ready()'s readiness checks.

`only_task` exists so a targeted run can demo one specific task without
reordering the queue — it must go through the identical readiness checks, never
around them.
"""

import dataclasses
import pytest
from burnkit.config import BurnConfig
from burnkit.queue import Task, branch_name, load_tasks, next_ready, read_frontmatter, task_md
from burnkit.state import bump_attempts, mark_handled
from pathlib import Path


def write_task(
    root: Path,
    rel_dir: str,
    task_id: str,
    *,
    ordinal: int,
    status: str = "To Do",
    deps: list[str] | None = None,
    title: str = "some task",
) -> Path:
    d = root / rel_dir
    d.mkdir(parents=True, exist_ok=True)
    deps_yaml = "[]" if not deps else "[" + ", ".join(deps) + "]"
    f = d / f"{task_id.lower()} - {title}.md"
    f.write_text(
        f"---\nid: {task_id}\ntitle: {title}\nstatus: {status}\ndependencies: {deps_yaml}\nordinal: {ordinal}\n---\n\nbody\n"
    )
    return f


@pytest.fixture
def backlog_root(tmp_path: Path, config: BurnConfig) -> Path:
    root = tmp_path / "mirror"
    write_task(root, config.tasks_dir, "WK-003.01", ordinal=5000)
    write_task(root, config.tasks_dir, "WK-009.10", ordinal=10010, deps=["WK-009.03"])
    write_task(root, config.dep_dirs[0], "WK-009.03", ordinal=9030, status="Done")
    return root


def test_read_frontmatter_parses_the_yaml_block(tmp_path: Path) -> None:
    f = write_task(tmp_path, "backlog/tasks", "WK-001.01", ordinal=7)
    assert read_frontmatter(f)["id"] == "WK-001.01"


def test_read_frontmatter_rejects_a_file_without_one(tmp_path: Path) -> None:
    f = tmp_path / "plain.md"
    f.write_text("# just a heading\n")
    with pytest.raises(ValueError):
        read_frontmatter(f)


def test_load_tasks_reads_the_dependency_directories_too(backlog_root: Path, config: BurnConfig) -> None:
    """A To Do task whose dependency landed in completed/ is ready, not blocked."""
    tasks = load_tasks(backlog_root, config.tasks_dir, config.dep_dirs)
    assert set(tasks) == {"WK-003.01", "WK-009.10", "WK-009.03"}
    assert tasks["WK-009.03"].status == "Done"


def test_load_tasks_lets_a_live_task_win_over_a_stale_copy(tmp_path: Path, config: BurnConfig) -> None:
    """A rename can leave a stale To Do copy behind in archive/; the live file
    in tasks_dir is the truth. dep_dirs order is precedence."""
    write_task(tmp_path, config.tasks_dir, "WK-004.01", ordinal=1, status="Done")
    write_task(tmp_path, config.dep_dirs[-1], "WK-004.01", ordinal=1, status="To Do")
    tasks = load_tasks(tmp_path, config.tasks_dir, config.dep_dirs)
    assert tasks["WK-004.01"].status == "Done"


def test_load_tasks_tolerates_a_missing_directory(tmp_path: Path, config: BurnConfig) -> None:
    write_task(tmp_path, config.tasks_dir, "WK-004.01", ordinal=1)
    assert set(load_tasks(tmp_path, config.tasks_dir, config.dep_dirs)) == {"WK-004.01"}


def test_default_pick_is_lowest_ordinal(backlog_root: Path, config: BurnConfig) -> None:
    assert next_ready(config, backlog_root) == "WK-003.01"


def test_only_task_overrides_the_ordinal_pick(backlog_root: Path, config: BurnConfig) -> None:
    assert next_ready(config, backlog_root, only_task="WK-009.10") == "WK-009.10"


def test_only_task_still_enforces_readiness(backlog_root: Path, config: BurnConfig) -> None:
    write_task(backlog_root, config.tasks_dir, "WK-009.99", ordinal=1, deps=["WK-999.99"])
    assert next_ready(config, backlog_root, only_task="WK-009.99") is None


def test_only_task_not_in_the_backlog_returns_none(backlog_root: Path, config: BurnConfig) -> None:
    assert next_ready(config, backlog_root, only_task="WK-999.01") is None


def test_phase_parents_are_not_pickable(tmp_path: Path, config: BurnConfig) -> None:
    """A parent (WK-002) is a container for its leaves (WK-002.01); there is
    nothing for an agent to do in it."""
    write_task(tmp_path, config.tasks_dir, "WK-002", ordinal=1)
    assert next_ready(config, tmp_path) is None


def test_skip_listed_tasks_are_never_picked(tmp_path: Path, config: BurnConfig) -> None:
    write_task(tmp_path, config.tasks_dir, "WK-005.01", ordinal=1)
    assert next_ready(config, tmp_path) == "WK-005.01"
    skipped = dataclasses.replace(config, skip_list=frozenset({"WK-005.01"}))
    assert next_ready(skipped, tmp_path) is None


def test_a_handled_task_is_not_handed_back_out(tmp_path: Path, config: BurnConfig) -> None:
    """The task's own commit sets its status to Done only inside an unpublished
    branch, so the queue source keeps reporting To Do until a human merges."""
    write_task(tmp_path, config.tasks_dir, "WK-005.01", ordinal=1)
    mark_handled(config.layout, "WK-005.01")
    assert next_ready(config, tmp_path) is None


def test_a_task_that_exhausted_its_attempts_stays_excluded(tmp_path: Path, config: BurnConfig) -> None:
    write_task(tmp_path, config.tasks_dir, "WK-005.01", ordinal=1)
    for _ in range(config.max_attempts):
        bump_attempts(config.layout, "WK-005.01")
    assert next_ready(config, tmp_path) is None


def test_a_task_with_an_unfinished_dependency_is_blocked(tmp_path: Path, config: BurnConfig) -> None:
    write_task(tmp_path, config.tasks_dir, "WK-006.01", ordinal=1, deps=["WK-006.00"])
    write_task(tmp_path, config.tasks_dir, "WK-006.00", ordinal=2, status="In Progress")
    assert next_ready(config, tmp_path) is None


def test_task_md_locates_a_task_file_in_a_worktree(tmp_path: Path, config: BurnConfig) -> None:
    write_task(tmp_path, config.tasks_dir, "WK-007.01", ordinal=1, title="do a thing")
    assert task_md(tmp_path, config.tasks_dir, "WK-007.01").name == "wk-007.01 - do a thing.md"


def test_task_md_raises_when_there_is_no_such_task(tmp_path: Path, config: BurnConfig) -> None:
    (tmp_path / config.tasks_dir).mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        task_md(tmp_path, config.tasks_dir, "WK-007.01")


def test_branch_name_slugifies_id_and_title() -> None:
    assert branch_name("WK-002.18", "Do the thing!") == "agent/wk-002-18-do-the-thing"


def test_branch_name_survives_an_empty_title() -> None:
    assert branch_name("WK-002.18", "") == "agent/wk-002-18"


def test_branch_name_truncates_a_long_slug() -> None:
    branch = branch_name("WK-002.18", "x" * 200)
    assert len(branch) < 70
    assert not branch.endswith("-")


def test_task_defaults_are_safe_for_a_missing_dependency() -> None:
    """next_ready() looks dependencies up in the loaded map; an id that isn't
    there must read as not-Done rather than raise."""
    assert Task(id="", status="").status != "Done"
