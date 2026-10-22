"""调度器中途崩溃作废的轮次不占轮次配额；其他失效照常计数。"""
import json

import pytest

from conftest import HANDOFF, crew
from crewlib import baseline
from crewlib.common import CrewError, crash_invalidated, read_json


def td_of(proj, task="t1"):
    return proj / "crew" / task


def fake_crash(proj, n, task="t1", k=1):
    """模拟调度器在审查员运行中崩溃：attempt 目录有过程记录、没有 meta 和结论。"""
    ad = td_of(proj, task) / f"round-{n:02d}" / "dsh" / f"attempt-{k:02d}"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n', encoding="utf-8")


def reply(proj, *ids, task="t1"):
    (td_of(proj, task) / "handoff.md").write_text(HANDOFF + "".join(f"{i} 已改\n" for i in ids), encoding="utf-8")


def test_crash_round_free_in_snapshot_and_gate(proj):
    for n in (1, 2):
        assert crew(proj, "snapshot", "t1")[0] == 0
        fake_crash(proj, n)
        code, out = crew(proj, "gate", "t1")
        assert code == 1 and "失效" in out
        rd = td_of(proj) / f"round-{n:02d}"
        assert read_json(rd / "INVALID.json")["kind"] == "scheduler_crash"
    assert baseline.counted_rounds(td_of(proj)) == 0
    assert crew(proj, "snapshot", "t1")[0] == 0                       # round-03，计费第 1 轮
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "退回" in out
    reply(proj, "dsh:R03:B1")
    assert crew(proj, "snapshot", "t1")[0] == 0                       # round-04，计费第 2 轮（上限 2）
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    code, out = crew(proj, "gate", "t1")
    assert code == 3 and "裁决" in out
    rep = read_json(td_of(proj) / "round-04" / "gate.json")
    assert rep["counted_rounds"] == 2 and rep["round_limit"] == 2
    reply(proj, "dsh:R03:B1", "dsh:R04:B1")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "上限" in out and "计入配额 2 轮" in out


def test_other_invalid_round_still_counts(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "write"})
    rd = td_of(proj) / "round-01"
    assert read_json(rd / "INVALID.json")["kind"] == "other"
    fake_crash(proj, 1, k=2)                                          # 之后再出现崩溃也不能改成免费
    crew(proj, "gate", "t1")
    assert read_json(rd / "INVALID.json")["kind"] == "other"
    assert baseline.counted_rounds(td_of(proj)) == 1
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    assert crew(proj, "gate", "t1")[0] == 3                           # 2 轮都计费，到上限


def test_source_change_invalid_counts(proj):
    crew(proj, "snapshot", "t1")
    (proj / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    assert crew(proj, "gate", "t1")[0] == 1
    assert read_json(td_of(proj) / "round-01" / "INVALID.json")["kind"] == "other"
    assert baseline.counted_rounds(td_of(proj)) == 1


def test_legacy_invalid_fallback(tmp_path):
    rd = tmp_path / "round-01"
    rd.mkdir()
    assert not crash_invalidated(rd)
    (rd / "INVALID").write_text("调度器在审查员运行中途崩溃（dsh/attempt-01），本轮作废\n", encoding="utf-8")
    assert crash_invalidated(rd)                                      # 旧数据：无 json，按措辞开头判断
    (rd / "INVALID").write_text("dsh attempt-01 审查期间发生写入/升权：调度器在审查员运行中途崩溃\n", encoding="utf-8")
    assert not crash_invalidated(rd)                                  # 只认开头，正文提到崩溃不算
    (rd / "INVALID").write_text("调度器在审查员运行中途崩溃\n", encoding="utf-8")
    (rd / "INVALID.json").write_text(json.dumps({"kind": "other"}), encoding="utf-8")
    assert not crash_invalidated(rd)                                  # 结构化记录优先
    for bad in ("{坏", '{"kind": 42}', '{"kind": null}', '{"kind": "crash"}', "[]", "{}"):
        (rd / "INVALID.json").write_text(bad, encoding="utf-8")
        with pytest.raises(CrewError, match="已损坏"):
            crash_invalidated(rd)


def test_invalid_commit_crash_between_text_and_json(proj):
    crew(proj, "snapshot", "t1")
    fake_crash(proj, 1)
    code, _ = crew(proj, "gate", "t1", env_extra={"CREW_CRASH_AT": "after_invalid_text"})
    assert code == -9
    rd = td_of(proj) / "round-01"
    assert (rd / "INVALID").exists() and not (rd / "INVALID.json").exists()
    assert crash_invalidated(rd) and baseline.counted_rounds(td_of(proj)) == 0
    code, out = crew(proj, "gate", "t1")                              # 再跑补齐孤儿登记，结果仍是失效
    assert code == 1 and (rd / "orphaned.json").exists()


def test_free_round_keeps_extra_approval_pending(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    assert crew(proj, "approve", "t1", "--kind", "extra_round", "--reason", "用户同意多给一轮")[0] == 0
    reply(proj, "dsh:R01:B1")
    crew(proj, "snapshot", "t1")                                      # round-02：计费第 2 轮，未超限
    fake_crash(proj, 2)
    crew(proj, "gate", "t1")
    crew(proj, "snapshot", "t1")                                      # round-03：崩溃轮免费，仍是计费第 2 轮
    base = read_json(td_of(proj) / "baseline.json")
    assert [a["applied"] for a in base["approvals"]] == [False]
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    reply(proj, "dsh:R01:B1", "dsh:R03:B1")
    assert crew(proj, "snapshot", "t1")[0] == 0                       # round-04：计费第 3 轮，用掉批准
    assert [a["applied"] for a in read_json(td_of(proj) / "baseline.json")["approvals"]] == [True]
