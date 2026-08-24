"""The task queue, read from Backlog.md task files.

Read from a mirror checkout rather than the user's own, so an overnight run
never touches a working tree a human might be using.
"""

import re
import yaml
from burnkit.config import BurnConfig
from burnkit.state import load_attempts, load_handled
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass
class Task:
    id: str
    status: str
    title: str = ""
    dependencies: list[str] = field(default_factory=list)
    parent_task_id: str | None = None
    ordinal: int = 0
    file: Path | None = None


def read_frontmatter(md: Path) -> dict:
    m = FRONTMATTER_RE.match(md.read_text())
    if not m:
        raise ValueError(f"no frontmatter in {md}")
    return yaml.safe_load(m.group(1)) or {}


def load_tasks(repo: Path, tasks_dir: str, dep_dirs: tuple[str, ...]) -> dict[str, Task]:
    tasks: dict[str, Task] = {}
    # tasks_dir first so a live task always wins over a same-id copy left behind
    # by a rename. First writer wins.
    for rel in (tasks_dir, *dep_dirs):
        for f in sorted((repo / rel).glob("*.md")):
            fm = read_frontmatter(f)
            tid = fm["id"]
            if tid in tasks:
                continue
            tasks[tid] = Task(
                id=tid,
                status=fm.get("status", ""),
                title=fm.get("title", ""),
                dependencies=fm.get("dependencies") or [],
                parent_task_id=fm.get("parent_task_id"),
                ordinal=fm.get("ordinal") or 0,
                file=f,
            )
    return tasks


def is_phase_parent(tasks: dict[str, Task], task_id: str) -> bool:
    """True if some other loaded task is a child of `task_id` -- via its own
    `parent_task_id` frontmatter field, or (the phase.subtask id convention,
    e.g. WK-002 / WK-002.01) a dotted id prefixed with `task_id`.

    A consumer whose backlog has no phase hierarchy at all (every task a flat,
    dot-less leaf with no children either way) never matches this, so its
    tasks remain pickable -- phase-parent exclusion is about having children,
    not about id shape.
    """
    return any(t.id != task_id and (t.parent_task_id == task_id or t.id.startswith(f"{task_id}.")) for t in tasks.values())


def next_ready(config: BurnConfig, repo: Path, only_task: str | None = None) -> str | None:
    """Pick the next ready leaf task, or — with `only_task` — check whether one
    specific task is ready and return it if so.

    The readiness checks are identical either way; `only_task` selects a
    different task, it never bypasses a check.
    """
    handled = load_handled(config.layout)
    attempts = load_attempts(config.layout)
    tasks = load_tasks(repo, config.tasks_dir, config.dep_dirs)
    ready = [
        t
        for t in tasks.values()
        if not is_phase_parent(tasks, t.id)
        and t.id not in config.skip_list
        and t.id not in handled
        and attempts.get(t.id, 0) < config.max_attempts  # exhausted its budget in a prior run
        and t.status == "To Do"
        and all(tasks.get(d, Task("", "")).status == "Done" for d in t.dependencies)
    ]
    if only_task is not None:
        return only_task if any(t.id == only_task for t in ready) else None
    if not ready:
        return None
    ready.sort(key=lambda t: (t.ordinal, t.id))
    return ready[0].id


def task_md(wt: Path, tasks_dir: str, task: str) -> Path:
    matches = sorted((wt / tasks_dir).glob(f"{task.lower()} - *.md"))
    if not matches:
        raise FileNotFoundError(f"no task file for {task}")
    return matches[0]


def branch_name(task: str, title: str) -> str:
    dashed_id = task.lower().replace(".", "-")  # WK-002.18 -> wk-002-18
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:40].rstrip("-")
    return f"agent/{dashed_id}-{slug}" if slug else f"agent/{dashed_id}"
