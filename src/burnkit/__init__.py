"""burnkit: a shared driver for overnight headless-agent burn loops over a
Backlog.md task queue.

A consumer supplies a BurnConfig and an integration strategy; burnkit owns the
queue, the worktree lifecycle, the launch backends, the finish-marker protocol,
and the proof-of-done gates.
"""

from burnkit.backend import (
    Backend,
    dsh_backend,
    hermes_backend,
    preflight_hooks_for,
    preflight_lemonade,
    resolve_backend,
    symlink_prepare,
)
from burnkit.cli import main, run_from_cli
from burnkit.config import BurnConfig, MachineGate, PreflightHook

# forensics is deliberately absent: it needs the `forensics` extra, so importing
# it here would make jq a hard requirement of `import burnkit`.
from burnkit.dshlog import iter_events, limited
from burnkit.gates import (
    TRUST_AGENT_ATTESTED,
    TRUST_MEASURED_LOCAL,
    GateReport,
    GateResult,
    Verdict,
    verify,
)
from burnkit.integration import FastForwardBranch, PullRequestPerTask
from burnkit.state import BurnLayout

__all__ = [
    "TRUST_AGENT_ATTESTED",
    "TRUST_MEASURED_LOCAL",
    "Backend",
    "BurnConfig",
    "BurnLayout",
    "FastForwardBranch",
    "GateReport",
    "GateResult",
    "MachineGate",
    "PreflightHook",
    "PullRequestPerTask",
    "Verdict",
    "dsh_backend",
    "hermes_backend",
    "iter_events",
    "limited",
    "main",
    "preflight_hooks_for",
    "preflight_lemonade",
    "resolve_backend",
    "run_from_cli",
    "symlink_prepare",
    "verify",
]
