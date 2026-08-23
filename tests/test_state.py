"""On-disk run state: the BURN directory layout, the heartbeat log, and the
two files that make the queue's decisions survive a process restart."""

import json
from burnkit.config import BurnConfig
from burnkit.state import (
    BurnLayout,
    bump_attempts,
    load_attempts,
    load_handled,
    mark_handled,
    read_fallback_planner,
    record_fallback_planner,
)


def test_layout_derives_every_path_from_one_root(config: BurnConfig) -> None:
    layout = config.layout
    assert layout.root == config.burn_dir
    for path in (
        layout.kill_file,
        layout.heartbeat,
        layout.mirror,
        layout.handled,
        layout.attempts,
        layout.review_queue,
        layout.pids,
        layout.logs,
        layout.prompts,
        layout.worktrees,
    ):
        assert config.burn_dir in path.parents or path == config.burn_dir


def test_ensure_dirs_creates_the_run_directories(config: BurnConfig) -> None:
    layout = config.layout
    layout.ensure_dirs()
    for path in (layout.pids, layout.logs, layout.prompts, layout.worktrees):
        assert path.is_dir()


def test_attempts_persist_to_disk_across_calls(config: BurnConfig) -> None:
    """In-memory-only counting resets on every process restart, silently granting
    a fresh retry budget to a task that already exhausted it."""
    layout = config.layout
    assert load_attempts(layout) == {}
    assert bump_attempts(layout, "WK-000.01") == 1
    assert bump_attempts(layout, "WK-000.01") == 2
    assert json.loads(layout.attempts.read_text()) == {"WK-000.01": 2}
    assert load_attempts(layout) == {"WK-000.01": 2}


def test_handled_tracks_tasks_this_driver_already_published(config: BurnConfig) -> None:
    layout = config.layout
    assert load_handled(layout) == set()
    mark_handled(layout, "WK-000.01")
    mark_handled(layout, "WK-000.02")
    assert load_handled(layout) == {"WK-000.01", "WK-000.02"}


def test_heartbeat_appends_one_parseable_line_per_call(config: BurnConfig) -> None:
    layout = config.layout
    layout.write_heartbeat("WK-000.01", 1, "start", "branch=agent/wk-000-01")
    layout.write_heartbeat("WK-000.01", 1, "end", "DONE")
    lines = layout.heartbeat.read_text().splitlines()
    assert len(lines) == 2
    assert "task=WK-000.01 attempt=1 phase=start" in lines[0]
    assert lines[1].endswith("status=DONE")


def test_layout_is_usable_without_a_full_config(tmp_path) -> None:
    """benchmark-style consumers reuse the layout alone, without building a
    whole BurnConfig."""
    layout = BurnLayout(tmp_path / "burn")
    assert layout.pids == tmp_path / "burn" / "pids"


def test_no_fallback_planner_is_recorded_by_default(config: BurnConfig) -> None:
    assert read_fallback_planner(config.layout) is None


def test_a_recorded_fallback_planner_round_trips(config: BurnConfig) -> None:
    """On disk rather than in memory for the same reason attempts are: a run
    that demoted its planner after three fast failures must stay demoted across
    a restart, or the next launch walks back into the same failing provider."""
    layout = config.layout
    record_fallback_planner(layout, "local-model", "local-provider")
    assert read_fallback_planner(layout) == ("local-model", "local-provider")
    assert layout.planner_override.exists()


def test_a_malformed_planner_override_reads_as_absent(config: BurnConfig) -> None:
    """A hand-edited or truncated sentinel must not crash a whole overnight run."""
    layout = config.layout
    layout.planner_override.parent.mkdir(parents=True, exist_ok=True)
    layout.planner_override.write_text("nonsense-with-no-separator\n")
    assert read_fallback_planner(layout) is None
