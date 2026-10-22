import json
import os
import time

import pytest

from conftest import HANDOFF, edit_cfg, hold_lock, make_project
from crewlib import fingerprint as fpm
from crewlib import issues, snapshot, verify
from crewlib.common import CrewError, read_json


def test_snapshot_creates_frozen_round(proj):
    r = snapshot.snapshot(proj, "t2")
    rd = proj / "crew" / "t2" / "round-01"
    fz = rd / "frozen"
    for name in ("crew.json", "handoff.md", "requirements.md", "rules.json", "hashes.json",
                 "open-issues-dsh.json", "open-issues-codex.json", "prompt-dsh.md", "prompt-codex.md"):
        assert (fz / name).exists(), name
    assert (rd / "fingerprint.txt").read_text().strip() == r["fingerprint"]
    prompt = (fz / "prompt-dsh.md").read_text(encoding="utf-8")
    assert f"CREW-META task=t2 round=1 fingerprint={r['fingerprint']}" in prompt
    assert "CREW-OPEN-ISSUES: 无" in prompt and "a + b" in prompt and "{{" not in prompt
    res = read_json(rd / "verify" / "01" / "result.json")
    assert res["ok"] and res["exit_code"] == 0
    assert read_json(rd / "verify" / "after.json")["fingerprint"] == r["fingerprint"]
    assert (proj / "crew" / "t2" / "baseline.json").exists()


def test_project_path_with_space_and_chinese(tmp_path):
    p = make_project(tmp_path / "cib_report_部署包 副本")
    r = snapshot.snapshot(p, "t1")
    assert r["round"] == 1 and r["verify"][0]["ok"]


def test_second_snapshot_never_overwrites(proj):
    snapshot.snapshot(proj, "t1")
    before = (proj / "crew" / "t1" / "round-01" / "manifest.txt").read_text()
    (proj / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    r2 = snapshot.snapshot(proj, "t1")
    assert r2["round"] == 2
    assert (proj / "crew" / "t1" / "round-01" / "manifest.txt").read_text() == before


@pytest.mark.parametrize("drop", snapshot.REQUIRED_HANDOFF)
def test_handoff_missing_section_refused(proj, drop):
    text = "\n".join(l for l in HANDOFF.splitlines() if not l.startswith(drop))
    (proj / "crew" / "t1" / "handoff.md").write_text(text, encoding="utf-8")
    with pytest.raises(CrewError, match=drop):
        snapshot.snapshot(proj, "t1")
    assert not (proj / "crew" / "t1" / "round-01").exists()


def test_symlink_refused(proj):
    os.symlink(proj / "calc.py", proj / "alias.py")
    with pytest.raises(CrewError, match="符号链接"):
        snapshot.snapshot(proj, "t1")


def test_verify_failure_recorded(tmp_path):
    p = make_project(tmp_path / "p", verify=["exit 3"])
    snapshot.snapshot(p, "t1")
    rd = p / "crew" / "t1" / "round-01"
    res = read_json(rd / "verify" / "01" / "result.json")
    assert res["exit_code"] == 3 and not res["ok"]
    rules = read_json(rd / "frozen" / "rules.json")
    fp = (rd / "fingerprint.txt").read_text().strip()
    assert any("未通过" in x for x in verify.problems(rd, rules, fp))


def test_verify_expect_text(tmp_path):
    p = make_project(tmp_path / "p", verify=[{"cmd": "echo 1 failed", "expect": "passed"}])
    r = snapshot.snapshot(p, "t1")
    assert not r["verify"][0]["ok"]


def test_verify_writing_scanned_file_invalidates(tmp_path):
    p = make_project(tmp_path / "p", verify=["echo '# x' >> calc.py"])
    snapshot.snapshot(p, "t1")
    rd = p / "crew" / "t1" / "round-01"
    rules = read_json(rd / "frozen" / "rules.json")
    fp = (rd / "fingerprint.txt").read_text().strip()
    assert any("验证命令写入" in x for x in verify.problems(rd, rules, fp))


def test_verify_missing_record_detected(proj):
    snapshot.snapshot(proj, "t1")
    rd = proj / "crew" / "t1" / "round-01"
    (rd / "verify" / "01" / "result.json").unlink()
    rules = read_json(rd / "frozen" / "rules.json")
    assert any("缺少执行记录" in x for x in verify.problems(rd, rules, (rd / "fingerprint.txt").read_text().strip()))


def test_verify_timeout_kills_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    p = make_project(tmp_path / "p", verify=[{"cmd": f"sleep 300 & echo $! > {pidfile}; wait",
                                              "timeout_minutes": 0.03}])
    t0 = time.monotonic()
    r = snapshot.snapshot(p, "t1")
    assert time.monotonic() - t0 < 30 and r["verify"][0]["timed_out"]
    child = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_lock_held_refuses_snapshot(proj):
    with hold_lock(proj / "crew" / "t1"):
        with pytest.raises(CrewError, match="另一个调度进程"):
            snapshot.snapshot(proj, "t1")
    assert not (proj / "crew" / "t1" / "round-01").exists()


def test_snapshot_freezes_verdict_schema(proj):
    snapshot.snapshot(proj, "t2")
    fz = proj / "crew" / "t2" / "round-01" / "frozen"
    assert "hardening" in (fz / "verdict.schema.json").read_text(encoding="utf-8")
    assert "verdict.schema.json" in json.loads((fz / "hashes.json").read_text(encoding="utf-8"))
