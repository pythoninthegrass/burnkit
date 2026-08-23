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
