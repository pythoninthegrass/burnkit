"""Shared fixtures.

Every consumer-specific value in the drivers burnkit was extracted from is a
`BurnConfig` field here, filled with deliberately generic placeholders. If a
test needs a real project's path, task-id namespace, or gate command to pass,
the abstraction leaked — fix the seam, don't widen the fixture.
"""

import pytest
from burnkit.config import BurnConfig, MachineGate
from pathlib import Path


@pytest.fixture
def gates() -> tuple[MachineGate, ...]:
    return (
        MachineGate(name="build", argv=("task", "build")),
        MachineGate(name="unit", argv=("task", "test")),
    )


@pytest.fixture
def config(tmp_path: Path, gates: tuple[MachineGate, ...]) -> BurnConfig:
    return BurnConfig(
        project="widget",
        burn_dir=tmp_path / "burn",
        repo=tmp_path / "repo",
        repo_slug="acme/widget",
        reviewer="acme-owner",
        author="pythoninthegrass",
        task_id_prefix="WK",
        example_task_id="WK-002.18",
        code_change_prefixes=("src/", "include/", "tests/", "specs/"),
        code_task_allowed_prefixes=("src/", "include/", "tests/", "specs/", "backlog/", "docs/"),
        machine_gates=gates,
        prompt_project_fragment=tmp_path / "project.txt",
        context7_line="Context7 is available for library documentation.",
    )
