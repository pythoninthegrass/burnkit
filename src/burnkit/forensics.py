"""`burn-dsh-log`: read a headless-agent session log after the fact.

The point of this over `zstd -dc | jq` is one streaming pass (see `dshlog`) and
a real streaming join for tool/call + tool/result instead of re-decompressing
the whole file per callId.

jq runs through the C bindings rather than a subprocess, which is why this
module sits behind the `forensics` extra and `dshlog` does not.

Not yet here, on purpose: joining a session log back to the burn run that
launched it. The key exists by construction -- the driver launches the agent
with cwd set to the task worktree, and the session directory is keyed by cwd --
and it would let a reviewer confirm a claimed gate command actually ran rather
than take the agent's word for it. That is a new capability, not part of moving
this reader, so it gets its own task.
"""

import argparse
import jq
import json
import re
import sys
from burnkit.dshlog import _apply_each, _jq_first, iter_events, limited
from collections import Counter
from pathlib import Path

# pyright: reportMissingImports=false


def _name_selector(name: str | None) -> str:
    return f" | select(.data.name=={json.dumps(name)})" if name else ""


def cmd_types(events, args) -> None:
    counts = Counter(jq.compile(".type").input_value(e).first() for e in events)
    for type_name, count in counts.most_common():
        print(f"{count:8d}  {type_name}")


def cmd_calls(events, args) -> None:
    program = jq.compile(
        'select(.type=="tool/call")'
        + _name_selector(args.name)
        + (f" | select(.data.turn=={args.turn})" if args.turn is not None else "")
        + (f" | select(.data.step=={args.step})" if args.step is not None else "")
        + " | {turn: .data.turn, step: .data.step, callId: .data.callId,"
        + "  name: .data.name, arguments: (.data.arguments | fromjson)}"
    )
    for record in limited(_apply_each(program, events), args.limit):
        print(json.dumps(record))


def cmd_joined(events, args) -> None:
    """Join in one pass: hold unmatched calls in `pending`, emit on the result.

    A call whose result never arrives is simply never emitted -- a truncated log
    (killed agent, full disk) is normal here, not an error.
    """
    call_filter = jq.compile(
        'select(.type=="tool/call")'
        + _name_selector(args.name)
        + " | {turn: .data.turn, step: .data.step, callId: .data.callId,"
        + "  name: .data.name, arguments: (.data.arguments | fromjson)}"
    )
    result_filter = jq.compile(
        'select(.type=="tool/result" and (.data | has("error") | not))'
        + " | {callId: .data.message.source.callId,"
        + "  text: .data.message.content[0].content[0].text}"
    )
    error_filter = jq.compile(
        'select(.type=="tool/result" and (.data | has("error")))' + " | {callId: .data.message.source.callId, error: .data.error}"
    )

    pending = {}
    emitted = 0
    for event in events:
        if args.limit is not None and emitted >= args.limit:
            break
        if event.get("type") == "tool/call":
            call = _jq_first(call_filter, event)
            if call is not None:
                pending[call["callId"]] = call
            continue
        if event.get("type") != "tool/result":
            continue

        result = _jq_first(result_filter, event)
        if result is None:
            result = _jq_first(error_filter, event)
        if result is None:
            continue

        call = pending.pop(result["callId"], None)
        if call is None:
            continue
        print(
            json.dumps(
                {
                    "turn": call["turn"],
                    "step": call["step"],
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "output": result.get("text", result.get("error")),
                }
            )
        )
        emitted += 1


def cmd_assistant(events, args) -> None:
    wanted_types = {"text", "reasoning"} if args.reasoning else {"text"}
    program = jq.compile(
        'select(.type=="assistant/message")' + " | {turn: .data.turn, step: .data.step, content: .data.message.content}"
    )
    for record in limited(_apply_each(program, events), args.limit):
        for item in record["content"]:
            if item.get("type") in wanted_types:
                print(f"[{record['turn']}/{record['step']}] {item['text']}")


def cmd_user(events, args) -> None:
    program = jq.compile('select(.type=="user/message") | .data.content[0].text')
    for text in limited(_apply_each(program, events), args.limit):
        print(text if args.full else text[:200])


def cmd_search(events, args) -> None:
    flags = re.IGNORECASE if args.ignore_case else 0
    pattern = re.compile(args.pattern, flags)
    count = 0
    for event in events:
        if args.limit is not None and count >= args.limit:
            break
        if args.type and event.get("type") != args.type:
            continue
        raw = json.dumps(event)
        if pattern.search(raw):
            print(raw)
            count += 1


def cmd_raw(events, args) -> None:
    program = jq.compile(args.filter)
    for record in limited(_apply_each(program, events), args.limit):
        if args.raw_output and isinstance(record, str):
            print(record)
        else:
            print(json.dumps(record))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="burn-dsh-log",
        description="Stream-parse a headless-agent session.jsonl(.zstd) log with jq bindings.",
    )
    parser.add_argument("session", type=Path, help="path to session.jsonl or session.jsonl.zstd")
    sub = parser.add_subparsers(dest="command", required=True)

    p_types = sub.add_parser("types", help="histogram of .type")
    p_types.add_argument("--limit", type=int, default=None)
    p_types.set_defaults(func=cmd_types)

    p_calls = sub.add_parser("calls", help="list tool/call events")
    p_calls.add_argument("--name", default=None)
    p_calls.add_argument("--turn", type=int, default=None)
    p_calls.add_argument("--step", type=int, default=None)
    p_calls.add_argument("--limit", type=int, default=None)
    p_calls.set_defaults(func=cmd_calls)

    p_joined = sub.add_parser("joined", help="stream-join tool/call + tool/result")
    p_joined.add_argument("--name", default=None)
    p_joined.add_argument("--limit", type=int, default=None)
    p_joined.set_defaults(func=cmd_joined)

    p_assistant = sub.add_parser("assistant", help="assistant message text per step")
    p_assistant.add_argument("--reasoning", action="store_true")
    p_assistant.add_argument("--limit", type=int, default=None)
    p_assistant.set_defaults(func=cmd_assistant)

    p_user = sub.add_parser("user", help="user message previews")
    p_user.add_argument("--full", action="store_true")
    p_user.add_argument("--limit", type=int, default=None)
    p_user.set_defaults(func=cmd_user)

    p_search = sub.add_parser("search", help="regex search over raw decoded lines")
    p_search.add_argument("pattern")
    p_search.add_argument("-i", "--ignore-case", action="store_true")
    p_search.add_argument("--type", default=None)
    p_search.add_argument("--limit", type=int, default=None)
    p_search.set_defaults(func=cmd_search)

    p_raw = sub.add_parser("raw", help="apply an arbitrary jq filter to every event")
    p_raw.add_argument("filter")
    p_raw.add_argument("-r", "--raw-output", action="store_true")
    p_raw.add_argument("--limit", type=int, default=None)
    p_raw.set_defaults(func=cmd_raw)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if not args.session.exists():
        print(f"error: {args.session} does not exist", file=sys.stderr)
        sys.exit(1)

    args.func(iter_events(args.session), args)


if __name__ == "__main__":
    main()
