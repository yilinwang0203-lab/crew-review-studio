"""decide：用户在「需要用户裁决」后接受现状；未关闭问题记进项目级已接受风险清单。"""
import json
import threading

import pytest

from conftest import HANDOFF, crew, edit_cfg
from crewlib import accepted_risks, decide
from crewlib.common import CrewError, read_json


def td_of(proj, task="t1"):
    return proj / "crew" / task


def reply(proj, *ids, task="t1"):
    (td_of(proj, task) / "handoff.md").write_text(HANDOFF + "".join(f"{i} 已改\n" for i in ids), encoding="utf-8")


def to_needs_user(proj, task="t1"):
    """t1 单审、上限 2 轮：连退两轮 → needs_user。"""
    crew(proj, "snapshot", task)
    crew(proj, "review", task, "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", task)
    reply(proj, "dsh:R01:B1", task=task)
    crew(proj, "snapshot", task)
    crew(proj, "review", task, "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    assert crew(proj, "gate", task)[0] == 3


def risks(proj):
    return accepted_risks.load(proj)["risks"]


def test_decide_cli_accepts_needs_user(proj):
    to_needs_user(proj)
    code, out = crew(proj, "decide", "t1", "--accept", "--reason", "用户接受 B1：只影响演示数据")
    assert code == 0 and "已记录用户裁决" in out
    d = read_json(td_of(proj) / "user_decision.json")
    assert d["round"] == 2 and d["decision"] == "accept" and d["reason"] == "用户接受 B1：只影响演示数据"
    assert d["by"] and d["at"]
    ids = [i["id"] for i in d["accepted_issues"]]
    assert "dsh:R02:B1" in ids and all(i["text"]["where"] for i in d["accepted_issues"])
    r = {x["id"]: x for x in risks(proj)}
    assert "t1:dsh:R02:B1" in r
    assert r["t1:dsh:R02:B1"]["source"] == {"task": "t1", "issue_id": "dsh:R02:B1"}
    assert r["t1:dsh:R02:B1"]["reason"] == d["reason"] and r["t1:dsh:R02:B1"]["text"]["where"] == "calc.py:2"


@pytest.mark.parametrize("setup,word", [
    ("none", "还没有送审"), ("pending", "需要用户裁决"), ("rejected", "需要用户裁决"),
    ("released", "需要用户裁决"), ("not_gated", "需要用户裁决"),
])
def test_decide_requires_latest_needs_user(proj, setup, word):
    if setup != "none":
        crew(proj, "snapshot", "t2" if setup == "pending" else "t1")
    if setup == "pending":
        crew(proj, "review", "t2", "--by", "dsh")
        crew(proj, "gate", "t2")
    elif setup == "rejected":
        crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
        crew(proj, "gate", "t1")
    elif setup == "released":
        crew(proj, "review", "t1", "--by", "dsh")
        crew(proj, "gate", "t1")
    task = "t2" if setup == "pending" else "t1"
    code, out = crew(proj, "decide", task, "--accept", "--reason", "x")
    assert code == 1 and word in out
    assert not (td_of(proj, task) / "user_decision.json").exists()
    assert not (proj / "crew" / "accepted-risks.json").exists()


def test_decide_revalidates_stale_gate(proj):
    to_needs_user(proj)
    (proj / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")   # 送审后又改了源码
    code, out = crew(proj, "decide", "t1", "--accept", "--reason", "x")
    assert code == 1 and "需要用户裁决" in out
    assert not (td_of(proj) / "user_decision.json").exists()


def test_decide_reason_and_action_required(proj):
    to_needs_user(proj)
    assert crew(proj, "decide", "t1", "--accept", "--reason", "   ")[0] == 1
    assert crew(proj, "decide", "t1", "--reason", "x")[0] == 2           # argparse：缺 --accept
    assert not (td_of(proj) / "user_decision.json").exists()


def test_decide_exhausted_without_issues(proj):
    crew(proj, "snapshot", "t1")
    for _ in range(3):
        crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "exit1"})
    assert crew(proj, "gate", "t1")[0] == 3
    d = decide.accept(proj, "t1", "审查员跑不出来，用户人工核对后接受")
    assert d["accepted_issues"] == [] and risks(proj) == []


def test_decide_does_not_release_or_close(proj):
    to_needs_user(proj)
    decide.accept(proj, "t1", "接受")
    assert not list(td_of(proj).glob("round-*/released.json"))
    st = {i["id"]: i["status"] for i in read_json(td_of(proj) / "issues.json")["issues"]}
    assert "closed" not in st.values()
    assert "t1" not in (proj / "crew" / "ledger.md").read_text(encoding="utf-8") \
        if (proj / "crew" / "ledger.md").exists() else True


def test_risk_dedup_and_two_tasks(proj):
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("review", "single"))
    to_needs_user(proj, "t1")
    to_needs_user(proj, "t2")
    decide.accept(proj, "t1", "a")
    decide.accept(proj, "t1", "a 再次确认")                                # 同来源重复：只保留首次
    decide.accept(proj, "t2", "b")
    ids = [r["id"] for r in risks(proj)]
    assert len(ids) == len(set(ids))
    assert "t1:dsh:R02:B1" in ids and "t2:dsh:R02:B1" in ids
    assert {r["id"]: r for r in risks(proj)}["t1:dsh:R02:B1"]["reason"] == "a"


def test_concurrent_sync_keeps_all(proj):
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("review", "single"))
    to_needs_user(proj, "t1")
    to_needs_user(proj, "t2")
    errs = []

    def go(t):
        try:
            decide.accept(proj, t, "并发")
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ths = [threading.Thread(target=go, args=(t,)) for t in ("t1", "t2")]
    [t.start() for t in ths]
    [t.join(60) for t in ths]
    assert not errs
    assert {"t1:dsh:R02:B1", "t2:dsh:R02:B1"} <= {r["id"] for r in risks(proj)}


def test_decide_crash_then_sync(proj):
    to_needs_user(proj)
    code, _ = crew(proj, "decide", "t1", "--accept", "--reason", "x", env_extra={"CREW_CRASH_AT": "after_decision"})
    assert code == -9
    assert (td_of(proj) / "user_decision.json").exists() and risks(proj) == []
    accepted_risks.sync(proj)
    accepted_risks.sync(proj)
    assert [r["id"] for r in risks(proj)].count("t1:dsh:R02:B1") == 1


def test_legacy_decision_skipped(proj):
    (td_of(proj) / "user_decision.json").write_text(json.dumps(
        {"round": 5, "decision": "accept", "at": "2026-10-13T17:10:00", "by": "用户", "reason": "旧手写"}),
        encoding="utf-8")
    assert accepted_risks.sync(proj)["risks"] == []

