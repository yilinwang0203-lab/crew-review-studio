import json

import pytest

from conftest import SKILL
from crewlib import issues, verdict

FP = "f" * 64


def good(**kw):
    v = {"task": "t1", "round": 1, "fingerprint": FP, "verdict": "pass", "blockers": [],
         "polish": [], "hardening": [], "prior_issues": [], "verification": []}
    v.update(kw)
    return v


BL = {"id": "B1", "summary": "加法算成了减法", "where": "calc.py:2", "trigger": "add(1,2)", "actual": "-1", "why": "应为和",
      "evidence": "return a - b", "speculative": False, "likelihood": "normal_use"}


def test_schema_matches_validator_fields():
    s = json.loads((SKILL / "schema" / "verdict.json").read_text(encoding="utf-8"))
    assert s["required"] == verdict.TOP and s["additionalProperties"] is False
    props = s["properties"]
    assert props["blockers"]["items"]["required"] == verdict.BLOCKER
    assert props["polish"]["items"]["required"] == verdict.POLISH
    assert props["hardening"]["items"]["required"] == verdict.HARDENING
    assert props["blockers"]["items"]["properties"]["likelihood"]["enum"] == list(verdict.LIKELIHOOD)
    assert props["prior_issues"]["items"]["required"] == verdict.PRIOR
    assert props["verification"]["items"]["required"] == verdict.VERIF


def test_extract_json_variants():
    assert verdict.extract_json('{"a": 1}') == {"a": 1}
    assert verdict.extract_json('说明文字\n```json\n{"a": 2}\n```\n结束') == {"a": 2}
    assert verdict.extract_json('先 {"a": 1} 后 {"a": 3}') == {"a": 3}
    nested = '结论如下：{"verdict": "reject", "blockers": [{"id": "B1"}]} 以上'
    assert verdict.extract_json(nested)["verdict"] == "reject"
    assert verdict.extract_json("{这不是 JSON") is None
    assert verdict.extract_json("") is None


def test_validate_ok_and_errors():
    assert verdict.validate(good(), "t1", 1, FP, []) == []
    assert verdict.validate(good(blockers=[BL], verdict="reject"), "t1", 1, FP, []) == []
    errs = verdict.validate(good(fingerprint="x"), "t1", 1, FP, [])
    assert any("指纹" in e for e in errs)
    assert any("缺少字段" in e for e in verdict.validate({"task": "t1"}, "t1", 1, FP, []))
    assert any("pass" in e for e in verdict.validate(good(blockers=[BL]), "t1", 1, FP, []))
    assert any("reject" in e for e in verdict.validate(good(verdict="reject"), "t1", 1, FP, []))
    assert any("B" in e for e in verdict.validate(good(verdict="reject", blockers=[dict(BL, id="X")]), "t1", 1, FP, []))


def test_validate_prior_issue_coverage():
    ids = ["dsh:R01:B1"]
    errs = verdict.validate(good(round=2), "t1", 2, FP, ids)
    assert any("漏表态" in e for e in errs)
    v = good(round=2, prior_issues=[{"id": "dsh:R01:B1", "decision": "close", "reason": "已修"}])
    assert verdict.validate(v, "t1", 2, FP, ids) == []
    v2 = good(round=2, prior_issues=[{"id": "dsh:R01:B1", "decision": "close", "reason": "已修"},
                                     {"id": "codex:R01:B1", "decision": "close", "reason": "越权"}])
    assert any("无权" in e for e in verdict.validate(v2, "t1", 2, FP, ids))


@pytest.mark.parametrize("bad", [
    {"round": "1"}, {"round": 1.0}, {"round": True}, {"verdict": ["pass"]},
    {"blockers": "x"}, {"blockers": [1]}, {"polish": [{"where": 1, "note": "n"}]},
    {"prior_issues": None}, {"verification": [{"command": "c", "status": "ok", "note": ""}]},
])
def test_validate_rejects_bad_types_without_crashing(bad):
    assert verdict.validate(good(**bad), "t1", 1, FP, []) != []


def test_render_diagnostic_never_assumes_fields():
    md = verdict.render_diagnostic({"round": "1", "blockers": "x"}, "{\"round\": \"1\"}", ["round 应为 1"], "dsh", 2)
    assert "格式错误" in md and "round 应为 1" in md


def test_render_md_chinese():
    md = verdict.render_md(good(verdict="reject", blockers=[BL]), "dsh", 1)
    assert "退回" in md and "calc.py:2" in md


def test_issue_lifecycle(tmp_path):
    td = tmp_path
    data = issues.load(td)
    issues.apply_verdict(data, "dsh", 1, 1, good(verdict="reject", blockers=[BL]), FP)
    assert issues.open_ids(data) == ["dsh:R01:B1"]
    assert issues.open_for(data, "codex") == []
    issues.record_replies(data, "dsh:R01:B1 已改 —— calc.py:2", 2)
    assert data["issues"][0]["replies"][-1]["lines"] == ["dsh:R01:B1 已改 —— calc.py:2"]
    close = good(round=2, prior_issues=[{"id": "dsh:R01:B1", "decision": "close", "reason": "已修"}])
    issues.apply_verdict(data, "dsh", 2, 1, close, FP)
    assert data["issues"][0]["status"] == "closing" and issues.open_ids(data) == []
    issues.revert_closing(data, "round-02 失效")
    assert data["issues"][0]["status"] == "open"
    issues.apply_verdict(data, "dsh", 2, 1, close, FP)
    issues.confirm_closing(data, 2)
    assert data["issues"][0]["status"] == "closed"
    assert [h.get("to") for h in data["issues"][0]["history"] if "to" in h] == ["open", "closing", "open", "closing", "closed"]
    issues.save(td, data)
    assert issues.load(td)["issues"][0]["id"] == "dsh:R01:B1"


def _round(td, n, handoff="", adopted=None, released=False, invalid=False, open_ids=None, legacy=False):
    """构造一轮带完整原始证据的记录（与 reviewers.run 写出的文件一致）。legacy=True 模拟 3.1 版之前、未冻结结论格式的旧轮次。"""
    rd = td / f"round-{n:02d}"
    (rd / "frozen").mkdir(parents=True)
    if not legacy:
        (rd / "frozen" / "verdict.schema.json").write_text(verdict.SCHEMA_PATH.read_text(encoding="utf-8"))
    (rd / "frozen" / "handoff.md").write_text(handoff, encoding="utf-8")
    (rd / "frozen" / "rules.json").write_text(json.dumps({"task": "t1", "reviewers": ["dsh", "codex"]}))
    for rev in ("dsh", "codex"):
        ids = (open_ids or {}).get(rev, [])
        (rd / "frozen" / f"open-issues-{rev}.json").write_text(json.dumps([{"id": i} for i in ids]))
    (rd / "fingerprint.txt").write_text(FP + "\n")
    for rev, v in (adopted or {}).items():
        ad = rd / rev / "attempt-01"
        ad.mkdir(parents=True)
        final = json.dumps(v, ensure_ascii=False)
        (ad / "verdict.json").write_text(final)
        (ad / "final.txt").write_text(final)
        events = [{"type": "session"}, {"type": "final", "text": final}] if rev == "dsh" else []
        (ad / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        if rev == "codex":
            (ad / "last-message.txt").write_text(final)
        (ad / "stderr.log").write_text("")
        (ad / "exit_code").write_text("0\n")
        (ad / "review.md").write_text("x")
        (ad / "meta.json").write_text(json.dumps({"reviewer": rev, "attempt": 1, "outcome": v["verdict"],
                                                  "errors": [], "violations": [], "exit_code": 0,
                                                  "timed_out": False}))
        (rd / rev / "adopted.json").write_text(json.dumps({"attempt": 1, "verdict": v["verdict"], "at": "t"}))
    if released:
        (rd / "released.json").write_text(json.dumps({"round": n, "at": "t"}))
    if invalid:
        (rd / "INVALID").write_text("x")
    return rd


def status(td):
    return {i["id"]: i["status"] for i in issues.rebuild(td)["issues"]}


def test_rebuild_replays_from_records(tmp_path):
    td = tmp_path
    _round(td, 1, adopted={"dsh": good(verdict="reject", blockers=[BL])})
    assert status(td) == {"dsh:R01:B1": "open"}
    close = good(round=2, prior_issues=[{"id": "dsh:R01:B1", "decision": "close", "reason": "已修"}])
    rd2 = _round(td, 2, handoff="dsh:R01:B1 已改", adopted={"dsh": close}, open_ids={"dsh": ["dsh:R01:B1"]})
    assert status(td) == {"dsh:R01:B1": "closing"}
    assert issues.rebuild(td)["issues"][0]["replies"][-1]["lines"] == ["dsh:R01:B1 已改"]
    (rd2 / "INVALID").write_text("x")
    assert status(td) == {"dsh:R01:B1": "open"}
    (rd2 / "INVALID").unlink()
    (rd2 / "released.json").write_text(json.dumps({"round": 2, "at": "t"}))
    assert status(td) == {"dsh:R01:B1": "closed"}
    assert issues.rebuild(td) == issues.rebuild(td)          # 重放幂等


def test_rebuild_reverts_closing_of_unreleased_round(tmp_path):
    td = tmp_path
    _round(td, 1, adopted={"dsh": good(verdict="reject", blockers=[BL])})
    close = good(round=2, prior_issues=[{"id": "dsh:R01:B1", "decision": "close", "reason": "已修"}])
    _round(td, 2, handoff="dsh:R01:B1 已改", adopted={"dsh": close}, open_ids={"dsh": ["dsh:R01:B1"]})
    _round(td, 3, handoff="dsh:R01:B1 已改")
    assert status(td) == {"dsh:R01:B1": "open"}


def test_rebuild_refuses_history_with_foreign_close(tmp_path):
    """越权关闭在 review 时就会被判格式错误；若历史采用记录里出现，说明记录被改过，重放必须停下。"""
    import pytest
    from crewlib.common import CrewError
    td = tmp_path
    _round(td, 1, adopted={"dsh": good(verdict="reject", blockers=[BL])})
    foreign = good(round=2, prior_issues=[{"id": "dsh:R01:B1", "decision": "close", "reason": "越权"}])
    _round(td, 2, handoff="dsh:R01:B1", adopted={"codex": foreign})
    with pytest.raises(CrewError, match="损坏"):
        issues.rebuild(td)


def test_rebuild_refuses_broken_adopted_record(tmp_path):
    """codex:R01:B3：损坏的历史采用记录不能静默跳过，否则会丢 blocker。"""
    import pytest
    from crewlib.common import CrewError
    td = tmp_path
    _round(td, 1, adopted={"dsh": good(verdict="reject", blockers=[BL])})
    rd2 = _round(td, 2, handoff="dsh:R01:B1")
    (rd2 / "dsh").mkdir()
    (rd2 / "dsh" / "adopted.json").write_text("{坏的")
    with pytest.raises(CrewError, match="损坏"):
        issues.rebuild(td)


def test_apply_is_idempotent(tmp_path):
    data = issues.load(tmp_path)
    v = good(verdict="reject", blockers=[BL])
    issues.apply_verdict(data, "dsh", 1, 1, v, FP)
    issues.apply_verdict(data, "dsh", 1, 1, v, FP)
    assert len(data["issues"]) == 1


# ---- 问题分轻重：blocker 标触发概率；极端条件问题进加固项、不拦放行 ----
HD = {"where": "recover.py:58", "trigger": "两次写入之间恰好再次崩溃", "note": "双重故障下的加固"}


def test_blocker_requires_likelihood():
    no = {k: v for k, v in BL.items() if k != "likelihood"}
    assert any("likelihood" in e for e in verdict.validate(good(verdict="reject", blockers=[no]), "t1", 1, FP, []))
    bad = dict(BL, likelihood="edge_case")
    assert any("likelihood" in e for e in verdict.validate(good(verdict="reject", blockers=[bad]), "t1", 1, FP, []))
    ok = dict(BL, likelihood="plausible_failure")
    assert verdict.validate(good(verdict="reject", blockers=[ok]), "t1", 1, FP, []) == []


def test_hardening_does_not_block_pass():
    assert verdict.validate(good(hardening=[HD]), "t1", 1, FP, []) == []
    assert any("hardening" in e for e in verdict.validate(good(hardening=[{"where": "x"}]), "t1", 1, FP, []))
    assert any("hardening" in e for e in verdict.validate(good(hardening="x"), "t1", 1, FP, []))


def test_render_shows_likelihood_and_hardening():
    md = verdict.render_md(good(verdict="reject", blockers=[BL], hardening=[HD]), "dsh", 1)
    assert "正常使用" in md and "加固项" in md and "双重故障" in md


# ---- 结论格式随轮次冻结：旧轮次按当时的格式校验（3.1 版加字段后历史记录不能被判损坏）----
def test_legacy_round_without_frozen_schema_replays(tmp_path):
    legacy = {k: v for k, v in good(verdict="reject").items() if k != "hardening"}
    legacy["blockers"] = [{k: v for k, v in BL.items() if k not in ("likelihood", "summary")}]
    _round(tmp_path, 1, adopted={"dsh": legacy}, legacy=True)
    assert status(tmp_path) == {"dsh:R01:B1": "open"}


def test_validate_legacy_mode():
    legacy = {k: v for k, v in good().items() if k != "hardening"}
    assert verdict.validate(legacy, "t1", 1, FP, [], severity=False) == []
    assert verdict.validate(legacy, "t1", 1, FP, []) != []          # 新版必须有 hardening


# ---- blocker 一句话摘要（2026-10-14 起新轮次必填；之前冻结的轮次照旧格式校验）----
def test_blocker_summary_required_in_new_format():
    no = {k: v for k, v in BL.items() if k != "summary"}
    assert any("summary" in e for e in verdict.validate(good(verdict="reject", blockers=[no]), "t1", 1, FP, []))
    assert verdict.validate(good(verdict="reject", blockers=[no]), "t1", 1, FP, [], summary=False) == []
    assert verdict.validate(good(verdict="reject", blockers=[BL]), "t1", 1, FP, []) == []
    assert "加法算成了减法" in verdict.render_md(good(verdict="reject", blockers=[BL]), "dsh", 1)


def test_round_frozen_before_summary_replays(tmp_path):
    old = json.loads(verdict.SCHEMA_PATH.read_text(encoding="utf-8"))
    old["properties"]["blockers"]["items"]["required"].remove("summary")
    del old["properties"]["blockers"]["items"]["properties"]["summary"]
    v = good(verdict="reject", blockers=[{k: x for k, x in BL.items() if k != "summary"}])
    _round(tmp_path, 1, adopted={"dsh": v})
    (tmp_path / "round-01" / "frozen" / "verdict.schema.json").write_text(json.dumps(old))
    assert not verdict.summary_of(tmp_path / "round-01") and verdict.severity_of(tmp_path / "round-01")
    assert status(tmp_path) == {"dsh:R01:B1": "open"}
