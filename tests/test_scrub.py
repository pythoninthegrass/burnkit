"""burnkit is a public repo consumed by private ones.

Nothing that names a consumer project, its task-id namespace, its asset trees,
or its reviewer may land here — those are values a consumer supplies through
BurnConfig, never burnkit defaults. Generic concepts are fine ("some worktree
content must never reach a remote planner"); naming what that content is, is
not.

This test is the mechanical form of that rule. It is deliberately a test rather
than a review habit, because the boundary it protects is load-bearing for the
consumers, not for burnkit.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Identifiers belonging to a specific consumer. This file is excluded from its
# own scan, which is why the list can name them plainly.
FORBIDDEN = [
    "azure",
    "zookinheimer",
    "mips_interp",
    "diff_verify",
    "retroarch",
    "splat",
    "extracted/",
    "analysis/local",
    "zelda3",
    "g_ram",
    "zig:parity",
]
# Task-id namespaces (AD-009.42, TASK-002.18, …).
FORBIDDEN_RE = [r"\bAD-\d", r"\bTASK-\d"]

SCANNED_SUFFIXES = {".py", ".toml", ".md", ".txt", ".yml", ".yaml", ".jsonc"}
SKIPPED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".tools"}


def _scanned_files() -> list[Path]:
    files = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIRS & set(path.relative_to(REPO).parts):
            continue
        if path == Path(__file__).resolve():
            continue
        files.append(path)
    return files


def test_no_consumer_private_identifiers_leaked() -> None:
    offenders = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        for needle in FORBIDDEN:
            if needle.lower() in lowered:
                offenders.append(f"{path.relative_to(REPO)}: {needle!r}")
        for pattern in FORBIDDEN_RE:
            if re.search(pattern, text):
                offenders.append(f"{path.relative_to(REPO)}: /{pattern}/")

    assert not offenders, "consumer-private identifiers must move to BurnConfig:\n" + "\n".join(offenders)


def test_scrub_actually_scans_something() -> None:
    """Guard against the scan silently matching zero files and passing vacuously."""
    assert len(_scanned_files()) > 3
