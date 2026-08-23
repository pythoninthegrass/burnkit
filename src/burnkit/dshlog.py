"""Streaming reader for a headless-agent session log (`session.jsonl(.zstd)`).

One decompression pass, one line-assembly loop, no re-reading the file per
query. That is the whole reason this exists: the ad hoc `zstd -dc | jq ...`
pipelines it replaces re-decompressed a multi-hundred-megabyte log once per
question asked of it, and a tool/call -> tool/result join done that way is
quadratic.

Deliberately jq-free. jq is what the CLI layer in `forensics.py` needs to
evaluate filters; a caller that only wants the events should not have to
install a C extension for it. `_apply_each` and `_jq_first` take an
already-compiled program and only touch its `input_value()` interface, so they
live here without importing jq themselves.

Session logs live under `~/.dsh/sessions/<project-dir>/<id>/`, where
`<project-dir>` is the path-encoded cwd. That encoding comes from
`projectKey()`/`encodeSegment()` in the upstream `dsh-session-persistence-jsonl`
package, not from anything here -- don't assume a `session-` prefix when
scripting against the path.
"""

import json
import zstandard
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

_READ_BLOCK = 1 << 20


def iter_events(path: Path) -> Iterator[Any]:
    """Yield decoded JSON events from a session.jsonl(.zstd) file, streaming."""
    if path.suffix == ".zstd":
        with path.open("rb") as fh:
            reader = zstandard.ZstdDecompressor().stream_reader(fh)
            yield from _iter_lines(reader)
    else:
        with path.open("rb") as fh:
            yield from _iter_lines(fh)


def _iter_lines(binary_stream) -> Iterator[Any]:
    """Assemble whole JSON lines out of fixed-size reads.

    A single tool/result can be larger than the read block, so the trailing
    partial line is carried into the next chunk rather than parsed.
    """
    buf = b""
    while True:
        chunk = binary_stream.read(_READ_BLOCK)
        if not chunk:
            break
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            line = line.strip()
            if line:
                yield json.loads(line)
    line = buf.strip()
    if line:
        yield json.loads(line)


def limited(iterable: Iterable[Any], limit: int | None) -> Iterator[Any]:
    """Stop after `limit` items, leaving the rest of the stream unread."""
    if limit is None:
        yield from iterable
        return
    count = 0
    for item in iterable:
        if count >= limit:
            return
        yield item
        count += 1


def _apply_each(program, events: Iterable[Any]) -> Iterator[Any]:
    for event in events:
        yield from program.input_value(event).all()


def _jq_first(program, value):
    """program.input_value(value).first(), but empty (select() with no match)
    raises StopIteration in this jq binding rather than returning None."""
    try:
        return program.input_value(value).first()
    except StopIteration:
        return None
