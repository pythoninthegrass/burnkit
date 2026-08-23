"""The seam between burnkit and a consumer.

One frozen dataclass carries everything project-specific. If a value or a
behavior differs between consumers it becomes a field or a registered callable
here — never a conditional inside the library. Nothing in burnkit reads module
state or environment variables directly; a consumer resolves its own
environment and hands the result over as a BurnConfig.
"""

from burnkit.state import BurnLayout
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class MachineGate:
    """A verification command burnkit runs itself, in the driver rather than in
    the agent — trust-but-verify.

    `applies=None` means unconditional. A consumer that selects gates from the
    diff supplies a predicate over the changed paths instead; both styles
    produce the same GateReport.
    """

    name: str
    argv: tuple[str, ...]
    applies: Callable[[list[str]], bool] | None = None


@dataclass(frozen=True)
class PreflightHook:
    """A content check run against a task's worktree before an agent launches.
    `check` returns offenders; a non-empty list aborts the launch.

    This is how a consumer enforces a boundary burnkit has no vocabulary for.
    Set `remote_planner_only` when the boundary is about what leaves the
    machine, so a fully-local backend is not asked to satisfy it.
    """

    name: str
    check: Callable[[Path], list[str]]
    remote_planner_only: bool = False


@dataclass(frozen=True)
class BurnConfig:
    project: str
    burn_dir: Path
    repo: Path

    # Publication
    repo_slug: str = ""
    base_branch: str = "main"
    reviewer: str = ""
    author: str = ""

    # Task queue
    task_id_prefix: str = ""
    example_task_id: str = ""
    tasks_dir: str = "backlog/tasks"
    # Finished tasks are filed out of tasks_dir into these, and still count as
    # dependencies. Order is precedence: a live task in tasks_dir wins over a
    # same-id copy here, and earlier entries win over later ones.
    dep_dirs: tuple[str, ...] = ("backlog/completed", "backlog/archive/tasks")
    skip_list: frozenset[str] = frozenset()

    # Models and endpoints
    model: str = ""
    provider: str = ""
    builder_model: str = ""
    builder_provider: str = ""
    lemonade_health_url: str = "http://127.0.0.1:13305/api/v0/health"
    secrets_env: Path | None = None
    dsh_env_file: Path | None = None
    default_backend: str = "dsh"

    # Secret name -> fallback value, resolved from `secrets_env` into the
    # launched agent's environment.
    launch_secrets: dict[str, str] = field(default_factory=dict)

    # Called with the selected backend's name before each task. Returning False
    # aborts the run; burnkit never learns what was checked.
    health_check: Callable[[str], bool] | None = None

    # Loop limits
    task_timeout_s: int = 3600
    max_attempts: int = 2
    max_turns: int = 150
    # An agent run dying faster than this suggests provider trouble rather than
    # task trouble, so it is counted separately from a normal failure.
    fast_fail_s: int = 90
    poll_s: int = 15

    # Gating
    code_change_prefixes: tuple[str, ...] = ()
    code_task_allowed_prefixes: tuple[str, ...] = ()
    machine_gates: tuple[MachineGate, ...] = ()
    preflight_hooks: tuple[PreflightHook, ...] = ()

    # Prompt
    prompt_project_fragment: Path | None = None
    prompt_backend_fragments: dict[str, Path] = field(default_factory=dict)
    context7_line: str = ""
    extra_bail_conditions: tuple[str, ...] = ()

    # Extra environment handed to a launched agent, keyed by task id. A
    # consumer uses this to point local tools at out-of-tree content.
    launch_env: Callable[[str], dict[str, str]] = lambda task: {}

    @property
    def layout(self) -> BurnLayout:
        return BurnLayout(self.burn_dir)

    def backend_fragment(self, backend_name: str) -> Path:
        """Where a backend's prompt fragment lives, defaulting to a sibling of
        the project fragment so a consumer keeps all its prompt text together."""
        explicit = self.prompt_backend_fragments.get(backend_name)
        if explicit is not None:
            return explicit
        base = self.prompt_project_fragment
        parent = base.parent if base is not None else self.repo
        return parent / f"prompt_header.{backend_name}.txt"
