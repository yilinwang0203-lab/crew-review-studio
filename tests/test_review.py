import json
import os
import shlex
import sys
import time

import pytest

from conftest import FAKE, HANDOFF, edit_cfg
from crewlib import issues, reviewers, snapshot
from crewlib.common import CrewError, read_json


@pytest.fixture
def fake_env(monkeypatch):
    fake = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    monkeypatch.setenv("CREW_DSH_BIN", fake)
    monkeypatch.setenv("CREW_CODEX_BIN", fake)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
    return monkeypatch


def rd_of(proj, task="t1", n=1):
    return proj / "crew" / task / f"round-{n:02d}"


def test_dsh_pass_adopted(proj, fake_env):
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "pass" and m["attempt"] == 1
    rv = rd_of(proj) / "dsh"
    assert read_json(rv / "adopted.json")["attempt"] == 1
    assert "通过" in (rv / "attempt-01" / "review.md").read_text(encoding="utf-8")
    with pytest.raises(CrewError, match="不能同轮重跑"):
        reviewers.run(proj, "t1", "dsh")


def test_codex_command_and_pass(proj, fake_env):
    snapshot.snapshot(proj, "t2")
    m = reviewers.run(proj, "t2", "codex")
    assert m["outcome"] == "pass"
    cmd = reviewers.command("codex", proj, rd_of(proj, "t2") / "codex" / "attempt-01")
    for flag in ("-a", "never", "exec", "-s", "read-only", "--ephemeral", "--skip-git-repo-check",
                 "--json", "--output-schema", "-o", "-C"):
        assert flag in cmd
    assert "resume" not in cmd


def test_reviewer_not_required_refused(proj, fake_env):
    snapshot.snapshot(proj, "t1")
    with pytest.raises(CrewError, match="不需要 codex"):
        reviewers.run(proj, "t1", "codex")


def test_reject_creates_issue(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "reject")
    snapshot.snapshot(proj, "t1")
    assert reviewers.run(proj, "t1", "dsh")["outcome"] == "reject"
    assert issues.open_ids(issues.load(proj / "crew" / "t1")) == ["dsh:R01:B1"]


@pytest.mark.parametrize("mode,word", [("empty", "没有输出"), ("garbage", "不是合法 JSON"),
                                       ("exit1", "退出码"), ("incomplete", "未完成")])
def test_incomplete_outcomes_then_exhausted(proj, fake_env, mode, word):
    fake_env.setenv("FAKE_MODE", mode)
    snapshot.snapshot(proj, "t1")
    for k in (1, 2, 3):
        m = reviewers.run(proj, "t1", "dsh")
        assert m["outcome"] == "incomplete" and m["attempt"] == k
        assert any(word in e for e in m["errors"])
    assert not (rd_of(proj) / "dsh" / "adopted.json").exists()
    with pytest.raises(CrewError, match="跑满"):
        reviewers.run(proj, "t1", "dsh")


@pytest.mark.parametrize("mode", ["round_str", "round_float", "blockers_str", "blockers_item"])
def test_malformed_verdict_recorded_not_crash(proj, fake_env, mode):
    fake_env.setenv("FAKE_MODE", mode)
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    ad = rd_of(proj) / "dsh" / "attempt-01"
    assert m["outcome"] == "incomplete" and m["errors"]
    assert read_json(ad / "meta.json")["outcome"] == "incomplete"
    assert (ad / "final.txt").read_text(encoding="utf-8")
    assert "格式错误" in (ad / "review.md").read_text(encoding="utf-8")
    assert not (ad / "verdict.json").exists()
    assert not (rd_of(proj) / "dsh" / "adopted.json").exists()


def test_timeout_kills_group_and_rerun_keeps_logs(tmp_path, fake_env):
    from conftest import make_project
    p = make_project(tmp_path / "p", timeouts={"dsh_minutes": 0.03, "codex_minutes": 1, "verify_minutes": 2})
    pidfile = tmp_path / "child.pid"
    fake_env.setenv("FAKE_MODE", "sleep")
    fake_env.setenv("FAKE_PIDFILE", str(pidfile))
    snapshot.snapshot(p, "t1")
    t0 = time.monotonic()
    m = reviewers.run(p, "t1", "dsh")
    assert time.monotonic() - t0 < 30 and m["timed_out"] and m["outcome"] == "incomplete"
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)
    fake_env.setenv("FAKE_MODE", "pass")
    m2 = reviewers.run(p, "t1", "dsh")
    assert m2["attempt"] == 2 and m2["outcome"] == "pass"
    rv = rd_of(p) / "dsh"
    assert read_json(rv / "attempt-01" / "meta.json")["timed_out"] is True
    assert read_json(rv / "adopted.json")["attempt"] == 2


def test_write_violation_invalidates_round(proj, fake_env):
    fake_env.setenv("FAKE_MODE", "write")
    snapshot.snapshot(proj, "t1")
    m = reviewers.run(proj, "t1", "dsh")
    assert m["outcome"] == "incomplete" and m["violations"]
    assert "写入" in (rd_of(proj) / "INVALID").read_text(encoding="utf-8")
    with pytest.raises(CrewError, match="已失效"):
        reviewers.run(proj, "t1", "dsh")


def test_dsh_scan_detects_only_completed_writes(tmp_path):
    ev = tmp_path / "e.jsonl"
    rows = [{"type": "tool_call", "callId": "a", "tool": "write", "input": {}},
            {"type": "tool_result", "callId": "a", "status": "error", "result": "denied"},
            {"type": "tool_call", "callId": "b", "tool": "read", "input": {"sandbox_permissions": "workspace-write"}},
            {"type": "tool_result", "callId": "b", "status": "completed", "result": "x"},
            {"type": "final", "text": "{}"}]
    ev.write_text("\n".join(json.dumps(r) for r in rows) + "\n不是JSON的一行\n")
    final, violations = reviewers.dsh_scan(ev)
    assert final == "{}" and len(violations) == 1 and "sandbox_permissions" in violations[0]


def test_open_issue_must_be_replied_and_attached(proj, fake_env):
    td = proj / "crew" / "t1"
    fake_env.setenv("FAKE_MODE", "reject")
    snapshot.snapshot(proj, "t1")
    reviewers.run(proj, "t1", "dsh")
    with pytest.raises(CrewError, match="dsh:R01:B1"):
        snapshot.snapshot(proj, "t1")
    (td / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 已改 —— calc.py:2\n", encoding="utf-8")
    snapshot.snapshot(proj, "t1")
    mine = json.loads((td / "round-02" / "frozen" / "open-issues-dsh.json").read_text(encoding="utf-8"))
    assert [i["id"] for i in mine] == ["dsh:R01:B1"]
    assert "dsh:R01:B1 已改" in (td / "round-02" / "frozen" / "prompt-dsh.md").read_text(encoding="utf-8")


def test_crash_between_adopted_and_issues_keeps_blocker(proj, fake_env):
    """在 adopted.json 写完、台账未写时 SIGKILL：下次任何命令都能从记录重放出 blocker。"""
    import subprocess
    from conftest import CREW
    td = proj / "crew" / "t1"
    snapshot.snapshot(proj, "t1")
    env = dict(os.environ, FAKE_MODE="reject", CREW_CRASH_AT="after_adopted")
    r = subprocess.run([sys.executable, str(CREW), "--project", str(proj), "review", "t1", "--by", "dsh"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == -9
    assert (td / "round-01" / "dsh" / "adopted.json").exists()
    assert json.loads((td / "issues.json").read_text(encoding="utf-8"))["issues"] == []   # 台账确实没写上
    with pytest.raises(CrewError, match="dsh:R01:B1"):      # 下一轮不回复旧问题就开不了
        snapshot.snapshot(proj, "t1")
    assert issues.open_ids(issues.load(td)) == ["dsh:R01:B1"]


def test_missing_key_clear_error_no_attempt(proj, fake_env):
    fake_env.setenv("DEEPSEEK_API_KEY", "")
    fake_env.setenv("CREW_KEYCHAIN_SERVICE", "crew-test-nonexistent-service")
    snapshot.snapshot(proj, "t1")
    with pytest.raises(CrewError, match="DeepSeek key"):
        reviewers.run(proj, "t1", "dsh")
    assert not (rd_of(proj) / "dsh" / "attempt-01").exists()


def test_codex_uses_frozen_schema(proj, fake_env):
    snapshot.snapshot(proj, "t2")
    reviewers.run(proj, "t2", "codex")
    meta = read_json(rd_of(proj, "t2") / "codex" / "attempt-01" / "meta.json")
    i = meta["command"].index("--output-schema")
    assert meta["command"][i + 1].endswith("round-01/frozen/verdict.schema.json")
