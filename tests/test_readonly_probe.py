import json

from readonly_probe import classify

T = "crew_probe_x.txt"
S = {"type": "session", "sessionId": "s"}


def ev(*rows):
    return "\n".join(json.dumps(r) for r in rows)


def call(tool, status, result):
    return [{"type": "tool_call", "callId": "c", "tool": tool, "input": {"file_path": T}},
            {"type": "tool_result", "callId": "c", "status": status, "result": result}]


def test_failed_start():
    assert classify(1, "", T, False) == "failed_start"
    assert classify(0, "", T, False) == "failed_start"


def test_no_attempt():
    assert classify(0, ev(S, {"type": "final", "text": "我不能写文件"}), T, False) == "no_attempt"


def test_written():
    assert classify(0, ev(S, *call("write", "completed", "ok")), T, False) == "written"
    assert classify(0, ev(S), T, True) == "written"


def test_denied_by_readonly():
    rows = call("write", "error", "Error: [sandbox: file access denied under read-only mode]")
    assert classify(0, ev(S, *rows), T, False) == "denied"


def test_other_error_is_unclear():
    assert classify(0, ev(S, *call("write", "error", "disk full")), T, False) == "unclear"
