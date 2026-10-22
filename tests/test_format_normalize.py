"""2026-10-22：审查结论格式笔误自动规整 + 格式不合规自动再审一次。
实测：t06 round-02 / t07 round-04 dsh 把 blocker 编号写成「dsh:R02:B1」；t06 round-03 加固项漏 trigger，都整轮作废。"""
import shlex
import sys

import pytest

from conftest import FAKE
from crewlib import reviewers, snapshot, verdict
from crewlib.common import read_json


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    fake = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    monkeypatch.setenv("CREW_DSH_BIN", fake)
    monkeypatch.setenv("CREW_CODEX_BIN", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("FAKE_SEQ_FILE", str(tmp_path / "seq-count"))
    return monkeypatch


def test_normalize_prefixed_blocker_id_and_missing_hardening_field():
    v = {"blockers": [{"id": "dsh:R04:B1"}, {"id": "R2-B3"}, {"id": "B2"}, {"id": "坏编号"}],
         "polish": [{"where": "a.py:1", "note": 3, "extra": "x"}],
         "hardening": [{"where": "a.py:1", "note": "n"}]}
    notes = verdict.normalize(v)
    assert [b["id"] for b in v["blockers"]] == ["B1", "B3", "B2", "坏编号"]       # 截不出 B 编号的不动，交校验
    assert v["polish"][0] == {"where": "a.py:1", "note": "3"}
    assert v["hardening"][0]["trigger"] == "（未写）"
    assert len(notes) == 5 and any("dsh:R04:B1" in n for n in notes)


def test_normalize_never_fills_blocker_fields():
    v = {"blockers": [{"id": "B1", "summary": "x"}]}
    verdict.normalize(v)
    assert v["blockers"][0] == {"id": "B1", "summary": "x"}


@pytest.mark.parametrize("mode,outcome", [("prefixed_id", "reject"), ("hardening_no_trigger", "pass")])
def test_real_format_slips_are_adopted_first_time(proj, fake_env, mode, outcome):
    fake_env.setenv("FAKE_MODE", mode)
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == outcome and m["attempt"] == 1 and m["normalized"]
    meta = read_json(proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01" / "meta.json")
    assert meta["normalized"] == m["normalized"]
    if mode == "prefixed_id":
        v = read_json(proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01" / "verdict.json")
        assert v["blockers"][0]["id"] == "B1"


def test_format_error_is_retried_automatically_once(proj, fake_env):
    fake_env.setenv("FAKE_SEQ", "garbage,pass")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "pass" and m["attempt"] == 2


def test_format_retry_only_once_and_blocker_missing_field_still_incomplete(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "blocker_no_trigger")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "incomplete" and m["attempt"] == 2                 # 自动再审一次，仍不合规就停
    assert any("缺少字段" in e for e in m["errors"])


@pytest.mark.parametrize("mode", ["incomplete", "exit1", "write"])
def test_non_format_failures_are_not_auto_retried(proj, fake_env, mode):
    fake_env.setenv("FAKE_MODE", mode)
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["attempt"] == 1


def test_format_only_classification():
    assert verdict.format_only(["blocker 编号必须形如 B1：'x'"])
    assert verdict.format_only(["最终结论不是合法 JSON"])
    assert not verdict.format_only(["超时（30 分钟），已终止整个进程组"])
    assert not verdict.format_only(["审查员自评未完成"])
    assert not verdict.format_only([])



@pytest.mark.parametrize("key", ["polish", "hardening"])
def test_misplaced_blocker_in_non_blocking_list_is_not_normalized(key):
    """审查 dsh:R01:B1：真问题误放进 polish/hardening（带 summary/actual/why 等），不删字段、不补字段，
    交 validate 判未完成（再审一次），不能降级成无害备注放行。"""
    item = {"where": "calc.py:2", "summary": "加法算成了减法", "trigger": "调用 add(1, 2)", "actual": "返回 -1",
            "why": "要求返回两数之和", "evidence": "return a - b", "speculative": False, "likelihood": "normal_use"}
    v = {"task": "t1", "round": 1, "fingerprint": "fp", "verdict": "pass", "blockers": [], "polish": [], "hardening": [],
         "prior_issues": [], "verification": []}
    v[key] = [dict(item)]
    assert verdict.normalize(v) == [] and v[key][0] == item
    errors = verdict.validate(v, "t1", 1, "fp", [])
    assert any("多余字段" in e or "缺少字段" in e for e in errors) and verdict.format_only(errors)
