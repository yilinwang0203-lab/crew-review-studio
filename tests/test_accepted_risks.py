"""项目级已接受风险清单：读取校验与从用户裁决同步。"""
import json

import pytest

from crewlib import accepted_risks
from crewlib.common import CrewError

ISSUE = {"id": "B1", "where": "calc.py:2", "trigger": "t", "actual": "a", "why": "w", "evidence": "e",
         "speculative": False, "likelihood": "normal_use"}


def write_decision(proj, task, issues, **kw):
    d = {"round": 2, "decision": "accept", "at": "2026-10-14T15:00:00", "by": "用户", "reason": "接受",
         "accepted_issues": [{"id": i, "text": ISSUE} for i in issues], **kw}
    (proj / "crew" / task / "user_decision.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def test_risk_missing_is_empty(proj):
    assert accepted_risks.load(proj) == {"risks": []}
    assert accepted_risks.sync(proj) == {"risks": []}
    assert not accepted_risks.path(proj).exists()          # 没有可同步的内容就不建文件


@pytest.mark.parametrize("bad", [
    "{坏", "[]", '{"risks": 1}', '{"risks": [null]}', '{"risks": ["x"]}',
    '{"risks": [{"source": {"task": "t1", "issue_id": "dsh:R01:B1"}}]}',
    '{"risks": [{"id": "t1:x", "text": {}, "reason": "r", "source": {"task": "t1"}}]}',
    '{"risks": [{"id": "t1:x", "text": "原文", "reason": "r", "source": {"task": "t1", "issue_id": "x"}}]}',
])
def test_risk_corrupt_refused_not_overwritten(proj, bad):
    p = accepted_risks.path(proj)
    p.write_text(bad, encoding="utf-8")
    write_decision(proj, "t1", ["dsh:R01:B1"])
    with pytest.raises(CrewError, match="accepted-risks.json"):
        accepted_risks.load(proj)
    with pytest.raises(CrewError, match="accepted-risks.json"):
        accepted_risks.sync(proj)
    assert p.read_text(encoding="utf-8") == bad


def test_sync_adds_with_source_and_dedups(proj):
    write_decision(proj, "t1", ["dsh:R01:B1", "codex:R02:B1"])
    write_decision(proj, "t2", ["dsh:R01:B1"], reason="t2 的理由")
    data = accepted_risks.sync(proj)
    assert sorted(r["id"] for r in data["risks"]) == ["t1:codex:R02:B1", "t1:dsh:R01:B1", "t2:dsh:R01:B1"]
    r = {x["id"]: x for x in data["risks"]}["t2:dsh:R01:B1"]
    assert r["source"] == {"task": "t2", "issue_id": "dsh:R01:B1"} and r["reason"] == "t2 的理由"
    assert r["text"] == ISSUE and r["at"] == "2026-10-14T15:00:00"
    before = accepted_risks.path(proj).read_bytes()
    accepted_risks.sync(proj)
    assert accepted_risks.path(proj).read_bytes() == before   # 再同步不重复、不重写


def test_sync_skips_legacy_and_non_accept(proj):
    (proj / "crew" / "t1" / "user_decision.json").write_text(
        '{"round": 5, "decision": "accept", "at": "x", "by": "用户", "reason": "旧手写"}', encoding="utf-8")
    write_decision(proj, "t2", ["dsh:R01:B1"], decision="other")
    assert accepted_risks.sync(proj)["risks"] == []


@pytest.mark.parametrize("bad", ["{坏", '{"decision": "accept", "accepted_issues": [null]}',
                                 '{"decision": "accept", "accepted_issues": [{"id": "x"}]}'])
def test_sync_corrupt_decision_refused(proj, bad):
    (proj / "crew" / "t1" / "user_decision.json").write_text(bad, encoding="utf-8")
    with pytest.raises(CrewError, match="user_decision.json"):
        accepted_risks.sync(proj)
    assert not accepted_risks.path(proj).exists()


# ---- 任务 4：冻结进每轮审查指令 ----
from conftest import crew  # noqa: E402
from crewlib.common import read_json  # noqa: E402


def frozen(proj, task, n=1):
    return proj / "crew" / task / f"round-{n:02d}" / "frozen"


def test_risks_frozen_for_both_reviewers(proj):
    write_decision(proj, "t1", ["dsh:R01:B1"], reason="演示数据，用户接受")
    assert crew(proj, "snapshot", "t2")[0] == 0
    fz = frozen(proj, "t2")
    assert read_json(fz / "accepted-risks.json")["risks"][0]["id"] == "t1:dsh:R01:B1"
    assert "accepted-risks.json" in read_json(fz / "hashes.json")
    for rev in ("dsh", "codex"):
        p = (fz / f"prompt-{rev}.md").read_text(encoding="utf-8")
        assert "t1:dsh:R01:B1" in p and "演示数据，用户接受" in p and "calc.py:2" in p
        assert "不得作为 blocker" in p and "polish" in p and "{{" not in p
    assert accepted_risks.load(proj)["risks"][0]["id"] == "t1:dsh:R01:B1"   # snapshot 顺带补齐了项目清单


def test_inserted_text_placeholders_kept_verbatim(proj):
    from crewlib import prompts
    risk = {"id": "t1:dsh:R01:B1", "reason": "接受 {{HANDOFF}}", "source": {"task": "t1", "issue_id": "dsh:R01:B1"},
            "text": dict(ISSUE, evidence="检查 {{REQUIREMENTS}} 和 {{HANDOFF}}")}
    for rev in ("dsh", "codex"):
        p = prompts.build(rev, "t2", 1, "f" * 64, "交接包内容 {{TASK}}", "要求内容 {{ACCEPTED_RISKS}}", [],
                          accepted_risks=[risk])
        assert "检查 {{REQUIREMENTS}} 和 {{HANDOFF}}" in p and "接受 {{HANDOFF}}" in p
        assert "交接包内容 {{TASK}}" in p and "要求内容 {{ACCEPTED_RISKS}}" in p
        assert "CREW-META task=t2 round=1" in p


def test_empty_risks_frozen(proj):
    assert crew(proj, "snapshot", "t1")[0] == 0
    fz = frozen(proj, "t1")
    assert read_json(fz / "accepted-risks.json") == {"risks": []}
    assert "accepted-risks.json" in read_json(fz / "hashes.json")
    p = (fz / "prompt-dsh.md").read_text(encoding="utf-8")
    assert "{{" not in p and "用户已接受的风险" in p


def test_live_risk_changes_do_not_invalidate(proj):
    crew(proj, "snapshot", "t1")
    write_decision(proj, "t2", ["dsh:R01:B1"])
    accepted_risks.sync(proj)                                       # 送审后项目清单变了
    crew(proj, "review", "t1", "--by", "dsh")
    assert crew(proj, "gate", "t1")[0] == 0
    assert read_json(frozen(proj, "t1") / "accepted-risks.json") == {"risks": []}


def test_frozen_risks_tamper_invalidates(proj):
    crew(proj, "snapshot", "t1")
    (frozen(proj, "t1") / "accepted-risks.json").write_text('{"risks": []} ', encoding="utf-8")
    crew(proj, "review", "t1", "--by", "dsh")
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "accepted-risks.json" in out


def test_gate_keeps_blocker_on_accepted_risk(proj):
    write_decision(proj, "t2", ["dsh:R01:B1"])
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})   # 审查员仍提同样问题
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "退回" in out                              # 只靠指令约束，gate 不自动过滤


def test_corrupt_risks_refuse_snapshot(proj):
    accepted_risks.path(proj).write_text("{坏", encoding="utf-8")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "accepted-risks.json" in out
    assert not (proj / "crew" / "t1" / "round-01").exists()
    assert not (proj / "crew" / "t1" / "baseline.json").exists()
