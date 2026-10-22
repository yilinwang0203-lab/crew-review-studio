import json

import pytest

from conftest import HANDOFF, crew, edit_cfg
from crewlib.common import read_json


def test_clean_pass_released_with_ledger(proj):
    assert crew(proj, "snapshot", "t1")[0] == 0
    assert crew(proj, "review", "t1", "--by", "dsh")[0] == 0
    code, out = crew(proj, "gate", "t1")
    assert code == 0 and "放行" in out
    ledger = (proj / "crew" / "ledger.md").read_text(encoding="utf-8")
    assert "t1 round-01 放行" in ledger and "可加文档字符串" in ledger
    assert read_json(proj / "crew" / "t1" / "round-01" / "gate.json")["status"] == "released"
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "已在 round-01 放行" in out


def test_source_change_after_review_blocks(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "内容变化 calc.py" in out


def test_reject_then_fix_next_round(proj):
    crew(proj, "snapshot", "t1")
    assert crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})[0] == 0
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "dsh:R01:B1" in out
    (proj / "crew" / "t1" / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 已改 —— calc.py:2\n", encoding="utf-8")
    assert crew(proj, "snapshot", "t1")[0] == 0
    crew(proj, "review", "t1", "--by", "dsh")
    assert crew(proj, "gate", "t1")[0] == 0
    iss = read_json(proj / "crew" / "t1" / "issues.json")["issues"][0]
    assert iss["status"] == "closed"


def test_round_limit_needs_user(proj):
    for n in (1, 2):
        if n == 2:
            (proj / "crew" / "t1" / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 不改 —— 理由\n", encoding="utf-8")
        crew(proj, "snapshot", "t1")
        crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    code, out = crew(proj, "gate", "t1")
    assert code == 3 and "用户" in out
    (proj / "crew" / "t1" / "handoff.md").write_text(
        HANDOFF + "dsh:R01:B1 已改\ndsh:R02:B1 已改\n", encoding="utf-8")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "上限" in out
    assert crew(proj, "approve", "t1", "--kind", "extra_round", "--reason", "用户同意再审一轮")[0] == 0
    assert crew(proj, "snapshot", "t1")[0] == 0


def test_double_requires_both_reviewers(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh")
    code, out = crew(proj, "gate", "t2")
    assert code == 2 and "codex" in out
    crew(proj, "review", "t2", "--by", "codex")
    assert crew(proj, "gate", "t2")[0] == 0


def _passed(proj, task="t1"):
    crew(proj, "snapshot", task)
    crew(proj, "review", task, "--by", "dsh")
    return proj / "crew" / task / "round-01" / "dsh"


@pytest.mark.parametrize("tamper", [
    lambda rv: (rv / "attempt-01" / "verdict.json").write_text('{"verdict": "pass", "fingerprint": "x", "polish": []}'),
    lambda rv: _edit_json(rv / "attempt-01" / "verdict.json", task="t9"),
    lambda rv: _edit_json(rv / "attempt-01" / "verdict.json", round=2),
    lambda rv: (rv / "attempt-01" / "meta.json").unlink(),
    lambda rv: _edit_json(rv / "attempt-01" / "meta.json", outcome="incomplete"),
    lambda rv: _edit_json(rv / "attempt-01" / "meta.json", exit_code=1),
    lambda rv: (rv / "attempt-01" / "exit_code").write_text("1\n"),
    lambda rv: _edit_json(rv / "attempt-01" / "meta.json", attempt=2),
    lambda rv: _edit_json(rv / "adopted.json", verdict="reject"),
    lambda rv: (rv / "attempt-01" / "final.txt").unlink(),
    lambda rv: (rv / "attempt-01" / "events.jsonl").unlink(),
    lambda rv: (rv / "attempt-01" / "final.txt").write_text(""),
    lambda rv: (rv / "attempt-01" / "final.txt").write_text('{"verdict": "pass"}'),
    lambda rv: (rv / "attempt-01" / "events.jsonl").write_text('{"type": "session"}\n{"type": "final", "text": "别的"}\n'),
    lambda rv: (rv / "attempt-01" / "stderr.log").unlink(),
])
def test_gate_revalidates_adopted_record(proj, tamper):
    rv = _passed(proj)
    tamper(rv)
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "dsh" in out


def _edit_json(path, **kw):
    d = json.loads(path.read_text(encoding="utf-8"))
    d.update(kw)
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_empty_stderr_is_not_blocked(proj):
    rv = _passed(proj)
    assert (rv / "attempt-01" / "stderr.log").read_text() == ""
    assert crew(proj, "gate", "t1")[0] == 0


def test_codex_last_message_mismatch_blocked(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh")
    crew(proj, "review", "t2", "--by", "codex")
    (proj / "crew" / "t2" / "round-01" / "codex" / "attempt-01" / "last-message.txt").write_text("{}")
    code, out = crew(proj, "gate", "t2")
    assert code == 1 and "last-message" in out


def _closing_round2(proj):
    """round-01 dsh 退回；round-02 dsh 关闭该问题（closing），尚未 gate。"""
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    (proj / "crew" / "t1" / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 已改\n", encoding="utf-8")
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")
    return lambda: {i["id"]: i["status"] for i in read_json(proj / "crew" / "t1" / "issues.json")["issues"]}


def test_crash_before_release_commit_does_not_close(proj):
    st = _closing_round2(proj)
    assert st() == {"dsh:R01:B1": "closing"}
    code, _ = crew(proj, "gate", "t1", env_extra={"CREW_CRASH_AT": "before_release_commit"})
    assert code == -9
    assert not (proj / "crew" / "t1" / "round-02" / "released.json").exists()
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    assert crew(proj, "gate", "t1")[0] == 1
    assert st() == {"dsh:R01:B1": "open"}


def test_crash_after_release_commit_recovers_once(proj):
    st = _closing_round2(proj)
    code, _ = crew(proj, "gate", "t1", env_extra={"CREW_CRASH_AT": "after_release_commit"})
    assert code == -9
    assert (proj / "crew" / "t1" / "round-02" / "released.json").exists()
    assert crew(proj, "gate", "t1")[0] == 0
    assert crew(proj, "gate", "t1")[0] == 0
    assert st() == {"dsh:R01:B1": "closed"}
    assert (proj / "crew" / "ledger.md").read_text(encoding="utf-8").count("t1 round-02 放行") == 1


def test_gate_rechecks_after_release_and_no_duplicate_ledger(proj):
    _passed(proj)
    assert crew(proj, "gate", "t1")[0] == 0
    assert crew(proj, "gate", "t1")[0] == 0                      # 文件未变：重复 gate 仍放行
    ledger = (proj / "crew" / "ledger.md").read_text(encoding="utf-8")
    assert ledger.count("t1 round-01 放行") == 1                  # 账本不重复追加
    (proj / "calc.py").write_text("def add(a, b):\n    return 0\n")
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "内容变化 calc.py" in out                # 放行后改源码：重新拦截


def test_gate_rechecks_after_release_handoff(proj):
    _passed(proj)
    assert crew(proj, "gate", "t1")[0] == 0
    (proj / "crew" / "t1" / "handoff.md").write_text(HANDOFF + "追加\n", encoding="utf-8")
    assert crew(proj, "gate", "t1")[0] == 1


def test_cli_refusal_exit_1(proj):
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "拒绝" in out and "snapshot" in out


def test_hardening_recorded_in_ledger_and_prompt_explains_severity(proj):
    crew(proj, "snapshot", "t1")
    prompt = (proj / "crew" / "t1" / "round-01" / "frozen" / "prompt-dsh.md").read_text(encoding="utf-8")
    assert "hardening" in prompt and "加固项" in prompt and "plausible_failure" in prompt
    crew(proj, "review", "t1", "--by", "dsh")
    assert crew(proj, "gate", "t1")[0] == 0
    ledger = (proj / "crew" / "ledger.md").read_text(encoding="utf-8")
    assert "加固项" in ledger and "加固建议示例" in ledger


def test_ledger_collects_suggestions_from_all_rounds(proj):
    """codex:R11:B1：退回轮次提出的加固项/polish 也要进账本，不能只收放行轮的。"""
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject", "FAKE_TAG": "R1"})
    crew(proj, "gate", "t1")
    (proj / "crew" / "t1" / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 已改\n", encoding="utf-8")
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_TAG": "R2"})
    assert crew(proj, "gate", "t1")[0] == 0
    ledger = (proj / "crew" / "ledger.md").read_text(encoding="utf-8")
    assert "加固建议示例R1" in ledger and "加固建议示例R2" in ledger
