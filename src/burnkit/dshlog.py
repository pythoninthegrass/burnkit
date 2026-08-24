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


_NARRATION_KEYS = frozenset({"description"})

STALL_WINDOW = 12
STALL_THRESHOLD = 0.75


def call_signature(name: str, arguments: Any) -> str:
    """Identity for "the agent already made this exact call".

    Keyed on the arguments that change what the call does. A free-text
    `description` is excluded: in the session this was built from, six commands
    that repeated byte-for-byte were relabeled "Render ..." then "Re-render
    ...", so including it would miss the very loop this is meant to catch.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            return f"{name}\n{arguments}"  # truncated log line; raw text is still stable
    if isinstance(arguments, dict):
        arguments = {k: v for k, v in arguments.items() if k not in _NARRATION_KEYS}
    return f"{name}\n{json.dumps(arguments, sort_keys=True, default=str)}"


def tool_call_signatures(path: Path) -> Iterator[str]:
    """Stream one signature per tool/call event in a session log."""
    for event in iter_events(path):
        if not isinstance(event, dict) or event.get("type") != "tool/call":
            continue
        data = event.get("data") or {}
        yield call_signature(data.get("name", ""), data.get("arguments"))


SESSIONS_ROOT = Path.home() / ".dsh" / "sessions"


def find_session_log(cwd: Path, *, sessions_root: Path = SESSIONS_ROOT, since: float | None = None) -> Path | None:
    """The newest session log written by an agent running in `cwd`, if any.

    Matched on the `cwd` the session event records rather than on the directory
    name. The name is dsh's path-encoding of that same cwd, so reading it back
    would couple this to an encoding that is not ours to depend on.

    `since` (epoch seconds) excludes logs created before the current attempt's
    launch: a worktree path is reused across attempts, and a leftover log from
    a prior (possibly killed) attempt is otherwise indistinguishable from this
    attempt's own -- not yet written -- log by cwd alone. Without this, a fresh
    attempt's first poll can inherit a previous attempt's already-stalled tail
    and be killed at zero progress of its own.
    """
    if not sessions_root.is_dir():
        return None
    candidates = [p for pat in ("*/*/session.jsonl", "*/*/session.jsonl.zstd") for p in sessions_root.glob(pat)]
    for path in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            head = next(iter_events(path), None)
        except (OSError, ValueError, zstandard.ZstdError):
            continue  # truncated or still being written; not this run's problem
        if not isinstance(head, dict) or head.get("cwd") != str(cwd):
            continue
        if since is not None and (created := head.get("createdAt")) is not None and created / 1000 < since:
            continue  # a prior attempt's log in the same worktree path
        return path
    return None


def stall_reason(
    signatures: Iterable[str],
    *,
    window: int = STALL_WINDOW,
    threshold: float = STALL_THRESHOLD,
) -> str | None:
    """Why the run looks stuck, or None if it still looks like work.

    A run is stuck when its most recent `window` calls are almost all repeats of
    calls already made: the outputs cannot have changed, so nothing new is
    reaching the agent. Only the trailing window counts, so an agent that breaks
    out of a cycle is not held to the cycle it escaped.

    Defaults are calibrated against the 72-call session this came from -- healthy
    exploration there peaked at a 0.17 repeat ratio, while the eight-command
    cycle it fell into passed 0.75 by call 49.
    """
    sigs = list(signatures)
    if len(sigs) < window:
        return None
    seen = set(sigs[:-window])
    repeats = 0
    for sig in sigs[-window:]:
        if sig in seen:
            repeats += 1
        seen.add(sig)
    ratio = repeats / window
    if ratio < threshold:
        return None
    return f"no progress: {repeats} of the last {window} tool calls repeat an earlier call ({ratio:.0%})"


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
