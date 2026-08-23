"""On-disk run state.

Everything lives under one root so a run is inspectable and disposable as a
unit, and so a second consumer on the same box cannot collide with the first.

The attempts and handled files exist because the alternatives are both wrong in
ways a real overnight run exposed — see the docstrings.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class BurnLayout:
    """Every path a run needs, derived from one root."""

    root: Path

    @property
    def kill_file(self) -> Path:
        return self.root / "KILL"

    @property
    def heartbeat(self) -> Path:
        return self.root / "status" / "state.log"

    @property
    def mirror(self) -> Path:
        """A read-only checkout of the base branch. The queue is read from here
        so the user's own checkout is never touched."""
        return self.root / "mirror"

    @property
    def handled(self) -> Path:
        return self.root / "handled.txt"

    @property
    def attempts(self) -> Path:
        return self.root / "attempts.json"

    @property
    def review_queue(self) -> Path:
        return self.root / "review-queue.md"

    @property
    def planner_override(self) -> Path:
        """Set when a run demotes its planner to the consumer's fallback."""
        return self.root / "PLANNER_OVERRIDE"

    @property
    def pids(self) -> Path:
        return self.root / "pids"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def prompts(self) -> Path:
        return self.root / "prompts"

    @property
    def worktrees(self) -> Path:
        return self.root / "wt"

    def ensure_dirs(self) -> None:
        for path in (self.logs, self.pids, self.prompts, self.heartbeat.parent, self.worktrees):
            path.mkdir(parents=True, exist_ok=True)

    def write_heartbeat(self, task: str, attempt: int, phase: str, status: str) -> None:
        self.heartbeat.parent.mkdir(parents=True, exist_ok=True)
        with self.heartbeat.open("a") as f:
            f.write(f"{now()} task={task} attempt={attempt} phase={phase} status={status}\n")


def load_handled(layout: BurnLayout) -> set[str]:
    if not layout.handled.exists():
        return set()
    return {line for line in layout.handled.read_text().splitlines() if line}


def mark_handled(layout: BurnLayout, task: str) -> None:
    """Record that this driver already published work for `task`.

    A task's own commit sets its status to Done only inside its unmerged
    branch, so the queue source keeps reporting To Do until a human merges.
    Without this, the queue hands the same task back out every loop while
    waiting on that merge.
    """
    layout.handled.parent.mkdir(parents=True, exist_ok=True)
    with layout.handled.open("a") as f:
        f.write(task + "\n")


def record_fallback_planner(layout: BurnLayout, model: str, provider: str) -> None:
    """Demote the planner for every subsequent launch in this run.

    On disk for the same reason attempts are: a run that gave up on its primary
    provider after repeated fast failures must stay demoted across a restart,
    or the next launch walks straight back into the failing provider.
    """
    layout.planner_override.parent.mkdir(parents=True, exist_ok=True)
    layout.planner_override.write_text(f"{model}|{provider}\n")


def read_fallback_planner(layout: BurnLayout) -> tuple[str, str] | None:
    """A malformed sentinel reads as absent rather than raising — this is read
    on every launch, and a hand-edited file should not end an overnight run."""
    if not layout.planner_override.exists():
        return None
    model, _, provider = layout.planner_override.read_text().strip().partition("|")
    return (model, provider) if model and provider else None


def load_attempts(layout: BurnLayout) -> dict[str, int]:
    if not layout.attempts.exists():
        return {}
    return json.loads(layout.attempts.read_text())


def bump_attempts(layout: BurnLayout, task: str) -> int:
    """Persist the attempt count to disk *before* the attempt runs.

    In-memory-only counting resets to zero on every process restart, so a human
    (or a supervisor) relaunching after a blocked exit would silently grant a
    fresh budget each time — the same unbounded-retry shape the attempt limit
    exists to prevent, just moved one level up.
    """
    attempts = load_attempts(layout)
    attempts[task] = attempts.get(task, 0) + 1
    layout.attempts.parent.mkdir(parents=True, exist_ok=True)
    layout.attempts.write_text(json.dumps(attempts))
    return attempts[task]
