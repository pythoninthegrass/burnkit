"""Proof-of-done gates and the trust classes that label their output.

The gates exist because an agent's own report of its work is not evidence. An
agent that hits its turn cap gets forced into a summary turn, where it can print
a DONE marker and *describe* bookkeeping it never performed; every gate after
the marker catches a variant of that.

Two tracks. A prose/decision task clears marker + commit + its own acceptance
criteria, and the result is labeled `agent_attested` — it still needs a human.
A code task additionally must clear machine gates that burnkit ran itself,
which is the only thing that earns `measured_local`.

Every function here takes its policy as an argument. None of them read module
state.
"""

import re
import subprocess
from burnkit import prompt
from burnkit.config import BurnConfig, MachineGate
from burnkit.proc import backlog, git
from burnkit.queue import Task, load_tasks, task_md
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

TRUST_AGENT_ATTESTED = "agent_attested"
TRUST_MEASURED_LOCAL = "measured_local"

_AC_HEADER_RE = re.compile(r"^## Acceptance Criteria\s*$", re.MULTILINE)
_AC_ITEM_RE = re.compile(r"^-\s*\[( |x|X)\]\s*#(\d+)\s+(.*)$")

_GATE_TIMEOUT_S = 1800


@dataclass
class GateResult:
    name: str
    ok: bool
    evidence: str


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """A zero-gate report is never a pass — that is the shape of a vacuous
        one, and it would read as evidence in a PR body."""
        return bool(self.results) and all(r.ok for r in self.results)

    def evidence_summary(self) -> str:
        return "; ".join(f"{r.name} {'pass' if r.ok else 'FAIL'}" for r in self.results)


@dataclass
class Verdict:
    ok: bool
    reason: str
    report: GateReport | None = None
    trust: str = TRUST_AGENT_ATTESTED


def _tail(text: str, n: int = 1500) -> str:
    text = text.strip()
    return text if len(text) <= n else "...\n" + text[-n:]


def _gate_run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=_GATE_TIMEOUT_S, check=False)


def changed_paths(wt: Path, base_sha: str) -> list[str]:
    out = git("diff", "--name-only", f"{base_sha}..HEAD", cwd=wt, check=False).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def is_code_change(paths: list[str], prefixes: tuple[str, ...]) -> bool:
    return any(p.startswith(pre) for p in paths for pre in prefixes)


def scope_violations(paths: list[str], allowed: tuple[str, ...]) -> list[str]:
    """A code task's diff should stay within its expected trees. Anything
    outside `allowed` is an agent that wandered off-task, not an
    implementation detail."""
    return [p for p in paths if not any(p.startswith(prefix) for prefix in allowed)]


def select_gates(gates: tuple[MachineGate, ...], changed: list[str]) -> tuple[MachineGate, ...]:
    """`applies=None` means unconditional, which is what a consumer running a
    fixed gate list registers."""
    return tuple(g for g in gates if g.applies is None or g.applies(changed))


def run_machine_gates(
    gates: tuple[MachineGate, ...],
    wt: Path,
    env: dict | None = None,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
) -> GateReport:
    """Run each gate in order, stopping at the first failure.

    Runs in the driver, not the agent — same trust-but-verify reason the marker
    gate exists.
    """
    run = run or _gate_run
    report = GateReport()
    for gate in gates:
        cp = run(list(gate.argv), wt, env)
        report.results.append(GateResult(gate.name, cp.returncode == 0, _tail((cp.stdout or "") + (cp.stderr or ""))))
        if cp.returncode != 0:
            break
    return report


def parse_acceptance_criteria(task_md_text: str) -> list[tuple[int, str, bool]]:
    """Parse a task file's own `## Acceptance Criteria` checklist.

    Backlog.md's Definition-of-Done defaults are commonly empty, so the per-task
    AC checklist is the closest thing to a structured definition of done there
    is to enforce.
    """
    header = _AC_HEADER_RE.search(task_md_text)
    if not header:
        return []
    section = task_md_text[header.end() :]
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]
    items = []
    for line in section.splitlines():
        m = _AC_ITEM_RE.match(line.strip())
        if m:
            items.append((int(m.group(2)), m.group(3).strip(), m.group(1).lower() == "x"))
    return items


def unchecked_acceptance_criteria(task_md_text: str) -> list[str]:
    return [f"#{n}" for n, _, checked in parse_acceptance_criteria(task_md_text) if not checked]


def ensure_backlog_done(config: BurnConfig, task: str, wt: Path) -> None:
    """Force the task's status to Done inside the worktree before publication.

    A turn-capped agent can print the DONE marker and describe having run
    `backlog task edit ... -s Done` without that call executing. Its work is
    already committed at that point — but the queue reads task status, so a task
    left "To Do" gets handed straight back out for a redundant re-run. Check and
    fix here rather than trust the self-report.
    """
    tasks = load_tasks(wt, config.tasks_dir, ())
    if tasks.get(task, Task("", "")).status == "Done":
        return
    backlog("task", "edit", task, "-s", "Done", cwd=wt, check=False)
    git("add", config.tasks_dir, cwd=wt, check=False)
    git(
        "commit",
        "-m",
        f"chore(backlog): force-mark {task} done (agent turn-capped before its own bookkeeping ran)",
        cwd=wt,
        check=False,
    )


def verify(
    config: BurnConfig,
    task: str,
    wt: Path,
    log: Path,
    base_sha: str,
    *,
    env: dict | None = None,
    run: Callable[..., subprocess.CompletedProcess] | None = None,
    ensure_done: Callable[[str, Path], None] | None = None,
) -> Verdict:
    """The whole gate, in the order the gates run.

    bail marker -> done marker -> at least one new commit -> every acceptance
    criterion checked -> (code tasks) diff within scope -> (code tasks) machine
    gates green.
    """
    ensure_done = ensure_done or partial(ensure_backlog_done, config)
    text = log.read_text(errors="replace") if log.exists() else ""
    finished, reason = prompt.parse_marker(text, task)
    if not finished:
        return Verdict(False, reason if reason == "no DONE marker" else f"bail: {reason}")
    commits = int(git("rev-list", "--count", f"{base_sha}..HEAD", cwd=wt).stdout.strip())
    if commits < 1:
        return Verdict(False, "no new commits")
    unchecked = unchecked_acceptance_criteria(task_md(wt, config.tasks_dir, task).read_text())
    if unchecked:
        return Verdict(False, f"unchecked acceptance criteria: {', '.join(unchecked)}")
    ensure_done(task, wt)
    changed = changed_paths(wt, base_sha)
    if not is_code_change(changed, config.code_change_prefixes):
        return Verdict(True, f"{commits} commits")
    violations = scope_violations(changed, config.code_task_allowed_prefixes)
    if violations:
        return Verdict(False, f"scope violation: {', '.join(violations)}", trust=TRUST_MEASURED_LOCAL)
    report = run_machine_gates(select_gates(config.machine_gates, changed), wt, env=env, run=run)
    if not report.ok:
        return Verdict(False, f"machine gate failed: {report.evidence_summary()}", report=report, trust=TRUST_MEASURED_LOCAL)
    return Verdict(True, f"{commits} commits; gates: {report.evidence_summary()}", report=report, trust=TRUST_MEASURED_LOCAL)
