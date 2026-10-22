"""snapshot 开新一轮前自动 gate 上一轮。"""
from conftest import HANDOFF, crew
from crewlib.common import read_json


def td_of(proj, task="t1"):
    return proj / "crew" / task


def history_len(proj, task, n):
    p = td_of(proj, task) / f"round-{n:02d}" / "gate-history.jsonl"
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def test_snapshot_auto_gate_missing_pass_releases_and_refuses(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "已在 round-01 放行" in out and "自动 gate" in out
    assert (td_of(proj) / "round-01" / "released.json").exists()
    assert not (td_of(proj) / "round-02").exists()


def test_snapshot_auto_gate_missing_pending_refuses(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh")                     # 双审只跑了 dsh
    base_before = (td_of(proj, "t2") / "baseline.json").read_bytes()
    code, out = crew(proj, "snapshot", "t2")
    assert code == 1 and "没审完" in out and "codex" in out
    assert not (td_of(proj, "t2") / "round-02").exists()
    assert (td_of(proj, "t2") / "baseline.json").read_bytes() == base_before


def test_snapshot_auto_gate_missing_reject_opens(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    (td_of(proj) / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 已改\n", encoding="utf-8")
    code, out = crew(proj, "snapshot", "t1", timeout=120)
    assert code == 0 and "round-02" in out
    rep = read_json(td_of(proj) / "round-01" / "gate.json")
    assert rep["status"] in ("rejected", "invalid")               # 没 gate 就改了 handoff，会被如实判失效


def test_snapshot_auto_gate_adopted_changed(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh")
    assert crew(proj, "gate", "t2")[0] == 2                       # pending
    crew(proj, "review", "t2", "--by", "codex")                   # gate 之后又有新结论
    code, out = crew(proj, "snapshot", "t2")
    assert code == 1 and "放行" in out
    assert (td_of(proj, "t2") / "round-01" / "released.json").exists()


def test_snapshot_auto_gate_legacy_report_without_signature(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")
    gp = td_of(proj) / "round-01" / "gate.json"
    gp.write_text('{"status": "pending", "pending": ["dsh"]}', encoding="utf-8")   # 旧版 gate 报告
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "放行" in out


def test_snapshot_auto_gate_unchanged_pending(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh")
    crew(proj, "gate", "t2")
    n = history_len(proj, "t2", 1)
    code, out = crew(proj, "snapshot", "t2")
    assert code == 1 and "没审完" in out and "自动 gate" not in out
    assert history_len(proj, "t2", 1) == n                        # 没有新结论就不重复 gate
    assert not (td_of(proj, "t2") / "round-02").exists()


def test_snapshot_after_gated_reject_does_not_regate(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    assert crew(proj, "gate", "t1")[0] == 1
    (proj / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    (td_of(proj) / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 已改\n", encoding="utf-8")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 0 and "自动 gate" not in out
    assert read_json(td_of(proj) / "round-01" / "gate.json")["status"] == "rejected"
    assert not (td_of(proj) / "round-01" / "INVALID").exists()   # 已 gate 过的退回轮不会因后来改文件被改判
