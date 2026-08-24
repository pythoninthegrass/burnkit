"""Streaming session-log reader.

The behavior worth pinning is the chunked read: `_iter_lines` pulls fixed-size
blocks and must not lose or split a record that straddles a block boundary.
Everything else here is contract-level -- what an empty jq `select()` returns,
what `limit=0` means -- because those are the edges the CLI depends on.

No jq import anywhere in this file: the core reader is stdlib + zstandard, and
these tests are the check that it stayed that way.
"""

import json
import pytest
import zstandard
from burnkit import dshlog
from pathlib import Path

EVENTS = [
    {"type": "user/message", "data": {"turn": 1}},
    {"type": "tool/call", "data": {"turn": 1, "name": "bash"}},
    {"type": "tool/result", "data": {"turn": 1}},
]


def write_jsonl(path: Path, events: list[dict], *, trailing_newline: bool = True) -> Path:
    body = "\n".join(json.dumps(e) for e in events) + ("\n" if trailing_newline else "")
    path.write_text(body)
    return path


def write_zstd(path: Path, events: list[dict]) -> Path:
    body = ("\n".join(json.dumps(e) for e in events) + "\n").encode()
    path.write_bytes(zstandard.ZstdCompressor().compress(body))
    return path


def test_iter_events_reads_plain_jsonl(tmp_path: Path) -> None:
    assert list(dshlog.iter_events(write_jsonl(tmp_path / "s.jsonl", EVENTS))) == EVENTS


def test_iter_events_decompresses_zstd(tmp_path: Path) -> None:
    assert list(dshlog.iter_events(write_zstd(tmp_path / "s.jsonl.zstd", EVENTS))) == EVENTS


def test_iter_events_handles_a_missing_trailing_newline(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "s.jsonl", EVENTS, trailing_newline=False)
    assert list(dshlog.iter_events(path)) == EVENTS


def test_iter_events_skips_blank_lines(tmp_path: Path) -> None:
    (tmp_path / "s.jsonl").write_text('\n\n{"type":"a"}\n\n{"type":"b"}\n\n')
    assert list(dshlog.iter_events(tmp_path / "s.jsonl")) == [{"type": "a"}, {"type": "b"}]


def test_iter_events_on_an_empty_file_yields_nothing(tmp_path: Path) -> None:
    (tmp_path / "s.jsonl").write_text("")
    assert list(dshlog.iter_events(tmp_path / "s.jsonl")) == []


def test_iter_lines_reassembles_a_record_split_across_read_boundaries(tmp_path: Path) -> None:
    """A single event larger than the read block must survive being pulled in
    pieces -- real session logs carry multi-megabyte tool results."""
    big = [{"type": "tool/result", "data": {"text": "x" * (3 << 20)}}, {"type": "done"}]
    path = write_jsonl(tmp_path / "s.jsonl", big)
    with path.open("rb") as fh:
        assert list(dshlog._iter_lines(fh)) == big


def test_limited_passes_everything_through_when_limit_is_none() -> None:
    assert list(dshlog.limited(range(5), None)) == [0, 1, 2, 3, 4]


def test_limited_truncates_at_the_limit() -> None:
    assert list(dshlog.limited(range(5), 2)) == [0, 1]


def test_limited_yields_nothing_at_zero() -> None:
    assert list(dshlog.limited(range(5), 0)) == []


def test_limited_stops_short_without_consuming_the_rest() -> None:
    """The point of the limit is not re-reading a whole session log."""
    consumed = []

    def counting():
        for i in range(100):
            consumed.append(i)
            yield i

    assert list(dshlog.limited(counting(), 3)) == [0, 1, 2]
    assert consumed == [0, 1, 2, 3]  # one lookahead, not 100


class FakeProgram:
    """Stands in for a compiled jq program so the core stays jq-free."""

    def __init__(self, results):
        self.results = results

    def input_value(self, value):
        outer = self

        class Result:
            def all(self):
                return list(outer.results)

            def first(self):
                if not outer.results:
                    raise StopIteration
                return outer.results[0]

        return Result()


def test_apply_each_flattens_every_program_output() -> None:
    assert list(dshlog._apply_each(FakeProgram(["a", "b"]), [1, 2])) == ["a", "b", "a", "b"]


def test_apply_each_drops_events_the_program_selects_away() -> None:
    assert list(dshlog._apply_each(FakeProgram([]), [1, 2, 3])) == []


def test_jq_first_returns_none_when_the_filter_matched_nothing() -> None:
    """`select()` with no match raises StopIteration in this binding rather than
    returning None, which would otherwise abort the caller's generator."""
    assert dshlog._jq_first(FakeProgram([]), {"type": "x"}) is None


def test_jq_first_returns_the_first_match() -> None:
    assert dshlog._jq_first(FakeProgram(["hit", "second"]), {"type": "x"}) == "hit"


def test_dshlog_does_not_import_jq() -> None:
    assert "import jq" not in Path(dshlog.__file__).read_text()


@pytest.mark.parametrize("suffix", [".jsonl", ".jsonl.zstd"])
def test_iter_events_dispatches_on_the_zstd_suffix(tmp_path: Path, suffix: str) -> None:
    path = tmp_path / f"session{suffix}"
    write_zstd(path, EVENTS) if suffix.endswith(".zstd") else write_jsonl(path, EVENTS)
    assert list(dshlog.iter_events(path)) == EVENTS


class TestCallSignatures:
    """Identity for "the agent already made this exact call".

    Keyed on the arguments that change what a call does. `description` is
    excluded deliberately: in the session that motivated this, six commands
    that repeated byte-for-byte were relabeled "Render ..." then "Re-render
    ...", so a signature including it would miss exactly the loop it exists to
    catch.
    """

    def test_the_same_call_has_the_same_signature(self) -> None:
        a = dshlog.call_signature("bash", '{"command": "ls", "description": "look"}')
        b = dshlog.call_signature("bash", '{"command": "ls", "description": "look"}')
        assert a == b

    def test_a_relabeled_repeat_is_still_the_same_call(self) -> None:
        a = dshlog.call_signature("bash", '{"command": "render A", "description": "Render A"}')
        b = dshlog.call_signature("bash", '{"command": "render A", "description": "Re-render A"}')
        assert a == b

    def test_key_order_does_not_change_the_signature(self) -> None:
        a = dshlog.call_signature("bash", '{"command": "ls", "timeout": 5}')
        b = dshlog.call_signature("bash", '{"timeout": 5, "command": "ls"}')
        assert a == b

    def test_a_different_command_is_a_different_call(self) -> None:
        a = dshlog.call_signature("bash", '{"command": "render A"}')
        b = dshlog.call_signature("bash", '{"command": "render B"}')
        assert a != b

    def test_the_same_arguments_to_a_different_tool_differ(self) -> None:
        a = dshlog.call_signature("bash", '{"path": "x"}')
        b = dshlog.call_signature("read", '{"path": "x"}')
        assert a != b

    def test_unparseable_arguments_fall_back_to_the_raw_text(self) -> None:
        # A truncated log line must not crash the poll loop that reads it.
        assert dshlog.call_signature("bash", '{"command": "ls"') == dshlog.call_signature("bash", '{"command": "ls"')

    def test_signatures_come_from_tool_calls_only(self, tmp_path: Path) -> None:
        log = write_jsonl(
            tmp_path / "s.jsonl",
            [
                {"type": "user/message", "data": {"turn": 1}},
                {"type": "tool/call", "data": {"name": "bash", "arguments": '{"command": "ls"}'}},
                {"type": "tool/result", "data": {"turn": 1}},
            ],
        )
        assert list(dshlog.tool_call_signatures(log)) == [dshlog.call_signature("bash", '{"command": "ls"}')]


class TestStallDetection:
    """Thresholds are calibrated against the real 72-call session this came
    from: healthy exploration peaked at a 0.17 repeat ratio, while the eight-
    command cycle it fell into drove the ratio past 0.75 by call 49."""

    def test_distinct_calls_never_stall(self) -> None:
        assert dshlog.stall_reason([f"call-{i}" for i in range(40)]) is None

    def test_a_short_run_is_never_judged(self) -> None:
        # Fewer calls than the window is not evidence of anything.
        assert dshlog.stall_reason(["a", "b", "a", "b"]) is None

    def test_a_fixed_cycle_is_reported(self) -> None:
        cycle = [f"call-{i}" for i in range(8)]
        assert dshlog.stall_reason(cycle * 6) is not None

    def test_the_reason_names_the_evidence(self) -> None:
        cycle = [f"call-{i}" for i in range(8)]
        reason = dshlog.stall_reason(cycle * 6)
        assert "12" in reason and "repeat" in reason.lower()

    def test_occasional_rechecks_do_not_trip_it(self) -> None:
        # Re-running one command every few calls is normal work, not a loop.
        sigs = []
        for i in range(40):
            sigs.append("recheck" if i % 4 == 0 else f"call-{i}")
        assert dshlog.stall_reason(sigs) is None

    def test_progress_after_a_cycle_clears_it(self) -> None:
        # Only the trailing window counts, so an agent that breaks out is not
        # punished for the loop it already escaped.
        cycle = [f"call-{i}" for i in range(8)] * 6
        assert dshlog.stall_reason(cycle + [f"new-{i}" for i in range(12)]) is None


class TestFindingASessionLog:
    """The path under ~/.dsh/sessions encodes the agent's cwd, but that encoding
    belongs to dsh, not here. Matching on the `cwd` the session event records
    means a change to the encoding cannot silently stop finding logs."""

    def _session(self, root: Path, name: str, cwd: str, events: list[dict] | None = None) -> Path:
        d = root / name / "session-1"
        d.mkdir(parents=True)
        head = {"type": "session", "id": "session-1", "cwd": cwd}
        return write_jsonl(d / "session.jsonl", [head] + (events or []))

    def test_it_matches_the_recorded_cwd_not_the_directory_name(self, tmp_path: Path) -> None:
        wanted = self._session(tmp_path, "encoded-in-some-other-way", "/work/wt/WK-001")
        self._session(tmp_path, "--work-wt-WK-002--", "/work/wt/WK-002")
        found = dshlog.find_session_log(Path("/work/wt/WK-001"), sessions_root=tmp_path)
        assert found == wanted

    def test_an_unknown_cwd_finds_nothing(self, tmp_path: Path) -> None:
        self._session(tmp_path, "a", "/work/wt/WK-002")
        assert dshlog.find_session_log(Path("/work/wt/WK-001"), sessions_root=tmp_path) is None

    def test_a_missing_sessions_root_is_not_an_error(self, tmp_path: Path) -> None:
        assert dshlog.find_session_log(Path("/work/wt"), sessions_root=tmp_path / "absent") is None

    def test_the_newest_session_for_a_cwd_wins(self, tmp_path: Path) -> None:
        # A retried task reuses its worktree, so several sessions can share a cwd.
        old = self._session(tmp_path, "old", "/work/wt/WK-001")
        new = self._session(tmp_path, "new", "/work/wt/WK-001")
        import os

        os.utime(old, (1, 1))
        os.utime(new, (2, 2))
        assert dshlog.find_session_log(Path("/work/wt/WK-001"), sessions_root=tmp_path) == new

    def test_an_unreadable_candidate_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        # A session still being written can be truncated mid-record.
        (tmp_path / "broken" / "session-1").mkdir(parents=True)
        (tmp_path / "broken" / "session-1" / "session.jsonl").write_text("{not json")
        wanted = self._session(tmp_path, "good", "/work/wt/WK-001")
        assert dshlog.find_session_log(Path("/work/wt/WK-001"), sessions_root=tmp_path) == wanted
