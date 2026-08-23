"""The consumer seam itself: what a BurnConfig resolves to on its own.

The values here are the ones a consumer cannot express as a plain string
because burnkit has to interpret them — which ref the base branch names, and
what "every path" means to the gate selectors.
"""

import dataclasses
from burnkit.config import ANY_PATH, BurnConfig, base_ref
from burnkit.gates import is_code_change, scope_violations


def test_base_ref_defaults_to_the_remote_tracking_branch(config: BurnConfig) -> None:
    assert base_ref(dataclasses.replace(config, base_branch="main")) == "origin/main"


def test_base_ref_honors_a_non_default_remote(config: BurnConfig) -> None:
    cfg = dataclasses.replace(config, base_branch="main", base_remote="upstream")
    assert base_ref(cfg) == "upstream/main"


def test_a_base_branch_with_no_remote_is_referenced_directly(config: BurnConfig) -> None:
    """A consumer whose shared branch deliberately never leaves the machine has
    no remote-tracking ref to resolve; asking for one fails every task."""
    cfg = dataclasses.replace(config, base_branch="feat/burn", base_remote="")
    assert base_ref(cfg) == "feat/burn"


def test_any_path_makes_every_task_a_code_task() -> None:
    """A consumer whose gates are unconditional says so with ANY_PATH rather
    than enumerating its whole tree and getting it wrong later."""
    assert is_code_change(["docs/notes.md"], ANY_PATH)
    assert is_code_change(["src/main.c"], ANY_PATH)


def test_any_path_still_treats_an_empty_diff_as_no_change() -> None:
    assert not is_code_change([], ANY_PATH)


def test_any_path_leaves_no_scope_violations() -> None:
    assert scope_violations(["wherever/it/went.txt"], ANY_PATH) == []
