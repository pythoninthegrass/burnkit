"""burnkit: a shared driver for overnight headless-agent burn loops over a
Backlog.md task queue.

A consumer supplies a BurnConfig and an integration strategy; burnkit owns the
queue, the worktree lifecycle, the launch backends, the finish-marker protocol,
and the proof-of-done gates.
"""

from burnkit.backend import (
    Backend,
    copy_prepare,
    dsh_backend,
    hermes_backend,
    planner,
    preflight_hooks_for,
    preflight_local_model,
    resolve_backend,
    symlink_prepare,
)
from burnkit.cli import main, run_from_cli
from burnkit.config import ANY_PATH, BurnConfig, MachineGate, PreflightHook, base_ref

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
from burnkit.integration import FastForwardBranch, PullRequestPerTask, default_integration
from burnkit.state import BurnLayout

__all__ = [
    "ANY_PATH",
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
    "base_ref",
    "copy_prepare",
    "default_integration",
    "dsh_backend",
    "hermes_backend",
    "iter_events",
    "limited",
    "main",
    "planner",
    "preflight_hooks_for",
    "preflight_local_model",
    "resolve_backend",
    "run_from_cli",
    "symlink_prepare",
    "verify",
]
