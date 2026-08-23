"""The `burn-dsh-log` CLI.

These tests need the `forensics` extra: six of the seven subcommands compile a
jq program, so the CLI as a whole depends on jq even though the reader core in
dshlog.py does not.
"""

import json
import pytest
from pathlib import Path

jq = pytest.importorskip("jq", reason="requires the [forensics] extra")

from burnkit import forensics  # noqa: E402  -- must follow the importorskip

SESSION = [
    {"type": "user/message", "data": {"turn": 1, "content": [{"text": "y" * 300}]}},
    {
        "type": "tool/call",
        "data": {"turn": 1, "step": 2, "callId": "c1", "name": "bash", "arguments": json.dumps({"cmd": "ls"})},
    },
    {
        "type": "tool/result",
        "data": {"turn": 1, "step": 3, "message": {"source": {"callId": "c1"}, "content": [{"content": [{"text": "out"}]}]}},
    },
    {
        "type": "tool/call",
        "data": {"turn": 2, "step": 4, "callId": "c2", "name": "edit", "arguments": json.dumps({"path": "a.py"})},
    },
    {"type": "tool/result", "data": {"turn": 2, "step": 5, "error": "boom", "message": {"source": {"callId": "c2"}}}},
    {
        "type": "assistant/message",
        "data": {
            "turn": 2,
            "step": 6,
            "message": {"content": [{"type": "reasoning", "text": "hmm"}, {"type": "text", "text": "hi"}]},
        },
    },
]


@pytest.fixture
def session(tmp_path: Path) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in SESSION))
    return path


def run(session: Path, argv: list[str], capsys) -> list[str]:
    forensics.main([str(session), *argv])
    return capsys.readouterr().out.splitlines()


def test_types_histograms_event_types(session, capsys) -> None:
    lines = run(session, ["types"], capsys)
    assert [ln.split()[1] for ln in lines if ln.strip()][0] == "tool/call"  # most common first
    assert any(ln.strip().startswith("2 ") or " 2  " in ln for ln in lines)


def test_calls_parses_the_arguments_json(session, capsys) -> None:
    records = [json.loads(ln) for ln in run(session, ["calls"], capsys)]
    assert [r["name"] for r in records] == ["bash", "edit"]
    assert records[0]["arguments"] == {"cmd": "ls"}  # decoded, not a string


def test_calls_filters_by_name(session, capsys) -> None:
    records = [json.loads(ln) for ln in run(session, ["calls", "--name", "edit"], capsys)]
    assert [r["callId"] for r in records] == ["c2"]


def test_calls_filters_by_turn_and_step(session, capsys) -> None:
    assert [json.loads(ln)["callId"] for ln in run(session, ["calls", "--turn", "2"], capsys)] == ["c2"]
    assert [json.loads(ln)["callId"] for ln in run(session, ["calls", "--step", "2"], capsys)] == ["c1"]


def test_joined_pairs_a_call_with_its_result(session, capsys) -> None:
    records = [json.loads(ln) for ln in run(session, ["joined"], capsys)]
    assert records[0]["name"] == "bash"
    assert records[0]["arguments"] == {"cmd": "ls"}
    assert records[0]["output"] == "out"


def test_joined_reports_an_errored_result_in_place_of_output(session, capsys) -> None:
    records = [json.loads(ln) for ln in run(session, ["joined"], capsys)]
    assert records[1]["output"] == "boom"


def test_joined_drops_a_result_whose_call_was_filtered_out(session, capsys) -> None:
    records = [json.loads(ln) for ln in run(session, ["joined", "--name", "bash"], capsys)]
    assert [r["name"] for r in records] == ["bash"]


def test_assistant_prints_text_and_skips_reasoning_by_default(session, capsys) -> None:
    assert run(session, ["assistant"], capsys) == ["[2/6] hi"]


def test_assistant_includes_reasoning_on_request(session, capsys) -> None:
    assert run(session, ["assistant", "--reasoning"], capsys) == ["[2/6] hmm", "[2/6] hi"]


def test_user_truncates_to_a_preview(session, capsys) -> None:
    assert len(run(session, ["user"], capsys)[0]) == 200


def test_user_full_prints_the_whole_message(session, capsys) -> None:
    assert len(run(session, ["user", "--full"], capsys)[0]) == 300


def test_search_matches_raw_lines(session, capsys) -> None:
    assert len(run(session, ["search", "a[.]py"], capsys)) == 1


def test_search_honors_ignore_case(session, capsys) -> None:
    assert run(session, ["search", "BASH"], capsys) == []
    assert len(run(session, ["search", "BASH", "-i"], capsys)) == 1


def test_search_restricts_to_one_event_type(session, capsys) -> None:
    assert len(run(session, ["search", "turn", "--type", "tool/call"], capsys)) == 2


def test_raw_applies_an_arbitrary_filter(session, capsys) -> None:
    assert run(session, ["raw", ".type", "--limit", "2"], capsys) == ['"user/message"', '"tool/call"']


def test_raw_output_unwraps_single_strings(session, capsys) -> None:
    assert run(session, ["raw", ".type", "-r", "--limit", "2"], capsys) == ["user/message", "tool/call"]


def test_limit_applies_to_every_command(session, capsys) -> None:
    assert len(run(session, ["calls", "--limit", "1"], capsys)) == 1
    assert len(run(session, ["joined", "--limit", "1"], capsys)) == 1
    assert len(run(session, ["search", "turn", "--limit", "2"], capsys)) == 2


def test_a_missing_session_file_exits_nonzero(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        forensics.main([str(tmp_path / "absent.jsonl"), "types"])
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_a_subcommand_is_required(session) -> None:
    with pytest.raises(SystemExit):
        forensics.main([str(session)])
