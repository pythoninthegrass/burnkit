"""Publication: how a passing attempt reaches a human, and how a failing one is
disposed of without destroying work.

Two strategies ship here because the drivers burnkit was extracted from differ
exactly at this point — one opens a pull request per task, the other
fast-forwards into a shared branch. Everything upstream of publication is
identical, which is why this is the only place the split lives.

`default_integration` fast-forwards by default: a PR that only re-states a
gate result the driver already ran tends to get rubber-stamped rather than
actually reviewed. `PullRequestPerTask` is still here for a consumer that
wants a human review step in the loop -- pass it explicitly to opt in.
"""

import dataclasses
import subprocess
from burnkit.config import BurnConfig
from burnkit.gates import TRUST_AGENT_ATTESTED, GateReport
from burnkit.proc import git
from burnkit.state import now
from dataclasses import dataclass
from pathlib import Path


def pr_body(
    config: BurnConfig,
    task: str,
    report: GateReport | None,
    *,
    trust: str = TRUST_AGENT_ATTESTED,
    launch_line: str | None = None,
) -> str:
    """The pull-request description.

    For a machine-gated task it carries the per-gate evidence so review starts
    from verified work; for any other task it records the marker+commit gate.
    Every body is labeled with its evidence trust class, so an agent-attested PR
    is never mistaken for a machine-verified one.
    """
    lines = [f"Automated by burnkit against backlog task `{task}`.", ""]
    if launch_line:
        lines += [launch_line, ""]
    lines.append(f"**Evidence trust class: `{trust}`**")
    if trust == TRUST_AGENT_ATTESTED:
        lines.append(
            "This task's evidence is agent-attested (marker + commit only -- no independent "
            "machine gate ran). Human confirmation is required before merge."
        )
    else:
        lines.append(f"This task's evidence is {trust}: it cleared the machine gates below.")
    if report is not None:
        lines += ["", "## Machine gates (all green before this PR opened)"]
        for r in report.results:
            lines.append(f"- **{r.name}**: {'pass' if r.ok else 'FAIL'}")
            if r.evidence:
                lines += ["  ```", "  " + "\n  ".join(r.evidence.splitlines()[-12:]), "  ```"]
    lines += ["", f"@{config.reviewer} please review.", ""]
    return "\n".join(lines)


def append_review_queue(queue_file: Path, task: str, branch: str, summary: str, *, trust: str = TRUST_AGENT_ATTESTED) -> None:
    """One line per publication, for the morning review pass, tagged with its
    evidence trust class."""
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with queue_file.open("a") as f:
        f.write(f"- {now()} `{task}` `{branch}` [{trust}] -- {summary}\n")


def retire_branch(repo: Path, branch: str, base_sha: str, task: str, attempt: int, *, published: bool = False) -> None:
    """Delete a task's attempt branch -- unless it carries commits beyond
    base_sha, in which case rename it to rescue/<task>.a<attempt> instead of
    force-deleting it. A failed attempt can still have produced real, committed
    work; only the reflog survives a bare `git branch -D`, which isn't enough to
    recover it before the next `git gc`.

    `published` skips the rescue: the commits are already reachable from
    whatever the integration strategy published, so a rescue ref would only
    accumulate one dead branch per successful task."""
    head = git("rev-parse", "--verify", branch, cwd=repo, check=False).stdout.strip()
    if head and head != base_sha and not published:
        rescue = f"rescue/{task}.a{attempt}"
        git("branch", "-D", rescue, cwd=repo, check=False)
        git("branch", "-m", branch, rescue, cwd=repo, check=False)
    else:
        git("branch", "-D", branch, cwd=repo, check=False)


@dataclass(frozen=True)
class PullRequestPerTask:
    """Push the attempt branch and open a pull request against the base branch
    with the configured reviewer requested. Nothing here mutates the consumer's
    own checkout."""

    config: BurnConfig
    name: str = "pull-request-per-task"

    def publish(
        self,
        task: str,
        title: str,
        branch: str,
        wt: Path,
        *,
        trust: str = TRUST_AGENT_ATTESTED,
        report: GateReport | None = None,
        launch_line: str | None = None,
    ) -> str | None:
        if git("push", "-u", "origin", branch, cwd=wt, check=False).returncode != 0:
            return None
        body = pr_body(self.config, task, report, trust=trust, launch_line=launch_line)
        summary = report.evidence_summary() if report is not None else "marker+commit (non-code task)"
        append_review_queue(self.config.layout.review_queue, task, branch, summary, trust=trust)
        r = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                self.config.repo_slug,
                "--base",
                self.config.base_branch,
                "--head",
                branch,
                "--title",
                title or task,
                "--body",
                body,
                "--reviewer",
                self.config.reviewer,
            ],
            cwd=wt,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return branch if r.returncode == 0 else None


def _ensure_publish_mirror(mirror: Path, source: Path) -> None:
    """Clone `source`'s own `origin` remote into `mirror`, if not already
    present there. Cloning from the remote rather than from `source` itself
    means a later `git push origin` lands on the real remote, not back into
    `source`'s own non-bare working copy."""
    if (mirror / ".git").exists():
        return
    origin_url = git("remote", "get-url", "origin", cwd=source).stdout.strip()
    mirror.parent.mkdir(parents=True, exist_ok=True)
    git("clone", origin_url, str(mirror), cwd=mirror.parent)
    git("checkout", "--detach", "HEAD", cwd=mirror)


@dataclass(frozen=True)
class FastForwardBranch:
    """Fast-forward a shared branch onto the attempt, refusing anything that
    isn't a fast-forward.

    Implemented as an ancestry check plus a ref move rather than `git merge
    --ff-only`, so publication does not depend on which branch the consumer's
    repo happens to have checked out.

    `publish_mirror`, if set, routes the ref move through a dedicated clone
    instead of operating on `config.repo` directly. Git refuses to
    force-move or fetch into a branch checked out in a sibling *worktree* of
    the same repo -- confirmed empirically, not from docs -- so
    fast-forwarding a branch a human might have checked out interactively
    (typically `base_branch`) needs a genuinely separate `.git`, not just
    another worktree of `config.repo`'s. Leave it unset when `config.repo`
    is already a dedicated, non-interactive location.
    """

    config: BurnConfig
    branch: str = "burn"
    push: bool = True
    publish_mirror: Path | None = None
    name: str = "fast-forward-branch"

    def publish(
        self,
        task: str,
        title: str,
        branch: str,
        wt: Path,
        *,
        trust: str = TRUST_AGENT_ATTESTED,
        report: GateReport | None = None,
        launch_line: str | None = None,
    ) -> str | None:
        if self.publish_mirror is not None:
            return self._publish_via_mirror(task, title, branch, wt, trust=trust, report=report, launch_line=launch_line)
        repo = self.config.repo
        sha = git("rev-parse", branch, cwd=repo, check=False).stdout.strip()
        if not sha:
            return None
        # A shared branch that diverged means someone else published in between;
        # moving the ref anyway would discard their work.
        if git("merge-base", "--is-ancestor", self.branch, sha, cwd=repo, check=False).returncode != 0:
            return None
        if git("branch", "--show-current", cwd=repo, check=False).stdout.strip() == self.branch:
            if git("merge", "--ff-only", sha, cwd=repo, check=False).returncode != 0:
                return None
        elif git("branch", "-f", self.branch, sha, cwd=repo, check=False).returncode != 0:
            return None
        summary = report.evidence_summary() if report is not None else "marker+commit (non-code task)"
        append_review_queue(self.config.layout.review_queue, task, self.branch, summary, trust=trust)
        if self.push:
            git("push", "origin", self.branch, cwd=repo, check=False)
        return self.branch

    def _publish_via_mirror(
        self,
        task: str,
        title: str,
        branch: str,
        wt: Path,
        *,
        trust: str,
        report: GateReport | None,
        launch_line: str | None,
    ) -> str | None:
        mirror = self.publish_mirror
        _ensure_publish_mirror(mirror, self.config.repo)
        # Refresh the mirror's view of `branch` from the real remote first --
        # otherwise the ancestry check below could run against a ref left
        # stale by an earlier publish() call in this same run.
        if git("fetch", "origin", self.branch, cwd=mirror, check=False).returncode != 0:
            return None
        if git("branch", "-f", self.branch, f"origin/{self.branch}", cwd=mirror, check=False).returncode != 0:
            return None
        # The mirror shares no objects with the worktree the attempt was
        # committed in, so its commits have to be fetched in explicitly.
        if git("fetch", str(self.config.repo), branch, cwd=mirror, check=False).returncode != 0:
            return None
        delegate = dataclasses.replace(self, config=dataclasses.replace(self.config, repo=mirror), publish_mirror=None)
        return delegate.publish(task, title, "FETCH_HEAD", wt, trust=trust, report=report, launch_line=launch_line)


def default_integration(config: BurnConfig) -> FastForwardBranch:
    """The default publish strategy: fast-forward `base_branch` straight
    through a dedicated mirror clone at `config.layout.publish_mirror`,
    never through `config.repo` itself.

    A PR that only re-states a gate result the driver already ran tends to
    get rubber-stamped rather than actually reviewed, so this -- not
    `PullRequestPerTask` -- is what a consumer gets by not choosing. Pass
    `PullRequestPerTask(config)` explicitly to opt back into a PR per task.
    """
    return FastForwardBranch(config, branch=config.base_branch, publish_mirror=config.layout.publish_mirror)
