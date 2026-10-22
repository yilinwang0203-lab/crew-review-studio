"""外部故障（容量不足/断网/额度）：识别、不计 attempt、同一次 review 内自动退避重试。"""
import json
import shlex
import sys
import threading
import time

import pytest

from conftest import FAKE, crew
from crewlib import external, gate, issues, recover, reviewers, snapshot
from crewlib.common import MAX_ATTEMPTS, CrewError, counted_attempts, next_attempt, read_json


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    fake = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    monkeypatch.setenv("CREW_DSH_BIN", fake)
    monkeypatch.setenv("CREW_CODEX_BIN", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    monkeypatch.setenv("CREW_EXTERNAL_BACKOFF_SCALE", "0")
    monkeypatch.setenv("FAKE_SEQ_FILE", str(tmp_path / "seq-count"))
    return monkeypatch


def rv_of(proj, task="t1", rev="dsh", n=1):
    return proj / "crew" / task / f"round-{n:02d}" / rev


def write_logs(ad, stderr="", events=()):
    ad.mkdir(parents=True)
    (ad / "stderr.log").write_text(stderr, encoding="utf-8")
    (ad / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


@pytest.mark.parametrize("stderr,events,kind,retryable", [
    ("", [{"type": "error", "message": "Selected model is at capacity."}], "capacity", True),
    ("ERROR: stream disconnected before completion", [], "network", True),
    ("Error: connect ECONNRESET 1.2.3.4:443", [], "network", True),
    ("", [{"type": "turn.failed", "error": {"message": "Rate limit reached"}}], "rate_limit", True),
    ("You've hit your usage limit", [], "quota", False),
    ("", [{"type": "error", "message": "insufficient_quota; stream disconnected"}], "quota", False),
    ("Insufficient Balance", [], "quota", False),
    ("Quota exceeded", [], "quota", False),
    ("", [{"type": "error", "message": "Reconnecting... 2/5 (unexpected status 503 Service Unavailable: retry)"},
          {"type": "turn.failed", "error": {"message": "exceeded retry limit, last status: 429 Too Many Requests"}}],
     "capacity", True),                                                   # 2026-10-14 todo4-t4 实测日志
    ("", [{"type": "turn.failed", "error": {"message": "last status: 429 Too Many Requests"}}], "rate_limit", True),
    ("quota exceeded; stream disconnected", [], "quota", False),
    ("Connection error.", [], "network", True),
    ("connect ECONNREFUSED / connection refused", [], "network", True),
    ("Could not resolve host: api.openai.com", [], "network", True),
])
def test_external_classification(tmp_path, stderr, events, kind, retryable):
    ad = tmp_path / "a"
    write_logs(ad, stderr, events)
    c = external.classify(ad)
    assert c["kind"] == kind and c["retryable"] is retryable and c["reason"]


def test_external_ignores_tool_output_and_bad_lines(tmp_path):
    ad = tmp_path / "a"
    ad.mkdir()
    (ad / "stderr.log").write_text("", encoding="utf-8")
    (ad / "events.jsonl").write_text(
        '{"type":"tool_result","result":"at capacity rate limit usage limit"}\n{"type":"err\n\xff\n',
        encoding="utf-8", errors="ignore")
    assert external.classify(ad) is None
    assert external.classify(tmp_path / "missing") is None


def test_external_does_not_replace_valid_verdict(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "tool_mentions")
    snapshot.snapshot(proj, "t1")
    assert reviewers.run(proj, "t1", "dsh")["outcome"] == "pass"


def test_garbage_with_keywords_is_plain_incomplete(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "tool_mentions")
    fake_env.setenv("FAKE_MENTION_GARBAGE", "1")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "incomplete" and m["attempt"] == 1


def test_external_violation_still_invalid(proj, fake_env):
    """故障日志与成功写入同时存在：本轮失效，不判外部故障、不自动重试。"""
    fake_env.setenv("FAKE_MODE", "ext_capacity")
    fake_env.setenv("FAKE_EXT_WRITE", "1")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "incomplete" and m["attempt"] == 1 and m["violations"]
    assert (rv_of(proj).parent / "INVALID").exists()
    assert gate.gate(proj, "t1")["status"] == "invalid"
    with pytest.raises(CrewError, match="已失效"):
        reviewers.run(proj, "t1", "dsh")


def test_external_retry_then_pass(proj, fake_env):
    fake_env.setenv("FAKE_SEQ", "ext_capacity,ext_net,pass")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "pass" and m["attempt"] == 3
    rv = rv_of(proj)
    assert read_json(rv / "adopted.json")["attempt"] == 3
    metas = [read_json(rv / f"attempt-0{k}" / "meta.json") for k in (1, 2)]
    assert [x["outcome"] for x in metas] == ["external", "external"]
    assert metas[0]["external_kind"] == "capacity" and metas[1]["external_kind"] == "network"
    assert all((rv / f"attempt-0{k}" / "stderr.log").exists() for k in (1, 2))
    assert counted_attempts(rv) == 1


def test_external_retry_budget_and_cli_exit(proj, fake_env, tmp_path):
    fake_env.setenv("CREW_EXTERNAL_BACKOFF_SCALE", "0.001")
    slept = []
    fake_env.setattr(reviewers, "_sleep", lambda s: slept.append(s))
    fake_env.setenv("FAKE_MODE", "ext_capacity")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "external" and m["attempt"] == 4
    assert slept == pytest.approx([0.06, 0.12, 0.24])
    assert counted_attempts(rv_of(proj)) == 0
    code, out = crew(proj, "review", "t1", "--by", "dsh",
                     env_extra={"FAKE_MODE": "ext_capacity", "CREW_EXTERNAL_BACKOFF_SCALE": "0"})
    assert code == 4 and "外部故障" in out
    assert next_attempt(rv_of(proj)) == 9


def test_quota_does_not_retry(proj, fake_env):
    slept = []
    fake_env.setattr(reviewers, "_sleep", lambda s: slept.append(s))
    fake_env.setenv("FAKE_MODE", "ext_quota")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "external" and m["external_kind"] == "quota" and m["attempt"] == 1
    assert slept == []


def test_external_counting_consistent(proj, fake_env):
    fake_env.setenv("FAKE_SEQ", "ext_capacity,exit1,ext_capacity,exit1,ext_net,exit1")
    snapshot.snapshot(proj, "t1")
    for _ in range(MAX_ATTEMPTS):
        assert reviewers.run(proj, "t1", "dsh")["outcome"] == "incomplete"
    rv = rv_of(proj)
    assert counted_attempts(rv) == MAX_ATTEMPTS and next_attempt(rv) == 7
    assert gate.gate(proj, "t1")["status"] == "needs_user"
    assert issues._dead(rv.parent)
    with pytest.raises(CrewError, match="跑满"):
        reviewers.run(proj, "t1", "dsh")


def test_pure_external_not_exhausted(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "ext_capacity")
    snapshot.snapshot(proj, "t1")
    reviewers.run(proj, "t1", "dsh")
    reviewers.run(proj, "t1", "dsh")
    r = gate.gate(proj, "t1")
    assert r["status"] == "pending" and r["exhausted"] == []
    assert not issues._dead(rv_of(proj).parent)


def test_external_recovery_is_terminal(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "ext_capacity")
    snapshot.snapshot(proj, "t1")
    fake_env.setenv("CREW_EXTERNAL_BACKOFF_SCALE", "0")
    reviewers.run(proj, "t1", "dsh")
    td = proj / "crew" / "t1"
    recover.recover(proj, "t1", td)
    assert not (rv_of(proj).parent / "INVALID").exists()
    fake_env.setenv("FAKE_MODE", "pass")
    assert reviewers.run(proj, "t1", "dsh")["outcome"] == "pass"


@pytest.mark.parametrize("bad", ["-1", "nan", "inf", "abc"])
def test_backoff_scale_validation(proj, fake_env, bad):
    snapshot.snapshot(proj, "t1")
    fake_env.setenv("CREW_EXTERNAL_BACKOFF_SCALE", bad)
    with pytest.raises(CrewError, match="CREW_EXTERNAL_BACKOFF_SCALE"):
        reviewers.run(proj, "t1", "dsh")
    assert not rv_of(proj).exists() or not list(rv_of(proj).glob("attempt-*"))


def test_backoff_scale_default():
    assert external.backoff_seconds({}) == [60, 120, 240]
    assert external.backoff_seconds({"CREW_EXTERNAL_BACKOFF_SCALE": "0"}) == [0, 0, 0]


def test_backoff_keeps_reviewer_lock_only(proj, fake_env):
    """退避等待期间：另一位审查员和 gate 可以运行，同一审查员被拒绝；普通未完成不自动重试。"""
    fake_env.setenv("FAKE_MODE_DSH", "ext_capacity")
    fake_env.setenv("FAKE_MODE_CODEX", "pass")
    snapshot.snapshot(proj, "t2")
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fake_sleep(s):
        calls.append(s)
        entered.set()
        release.wait(30)
    fake_env.setattr(reviewers, "_sleep", fake_sleep)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("m", reviewers.run(proj, "t2", "dsh")))
    th.start()
    try:
        assert entered.wait(30)
        with pytest.raises(CrewError, match="已经在审查"):
            reviewers.run(proj, "t2", "dsh")
        assert reviewers.run(proj, "t2", "codex")["outcome"] == "pass"
        assert gate.gate(proj, "t2")["status"] == "pending"
        with pytest.raises(CrewError, match="还在审查"):
            snapshot.snapshot(proj, "t2")
    finally:
        fake_env.setenv("FAKE_MODE_DSH", "pass")
        release.set()
        th.join(60)
    assert out["m"]["outcome"] == "pass"
    calls.clear()
    fake_env.delenv("FAKE_MODE_DSH")
    fake_env.setenv("FAKE_MODE", "exit1")
    snapshot.snapshot(proj, "t1")
    assert reviewers.run(proj, "t1", "dsh")["outcome"] == "incomplete" and calls == []
