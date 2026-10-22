"""spec §13 流程测试 1–21（第 10 项在 test_real.py，需真实 dsh）。"""
import os
import time

import pytest

from conftest import HANDOFF, crew, edit_cfg, hold_lock, make_project
from crewlib.common import read_json


def handoff(proj, task, extra):
    (proj / "crew" / task / "handoff.md").write_text(HANDOFF + extra, encoding="utf-8")


def iss(proj, task):
    return {i["id"]: i for i in read_json(proj / "crew" / task / "issues.json")["issues"]}


def test_01_clean_project_released(proj):
    crew(proj, "snapshot", "t1"); crew(proj, "review", "t1", "--by", "dsh")
    assert crew(proj, "gate", "t1")[0] == 0


def test_02_source_change_kept_to_gate(proj):
    crew(proj, "snapshot", "t1"); crew(proj, "review", "t1", "--by", "dsh")
    (proj / "calc.py").write_text("def add(a, b):\n    return 0\n")
    assert crew(proj, "gate", "t1")[0] == 1


def test_03_and_12_crew_json_changed_after_snapshot(proj):
    crew(proj, "snapshot", "t2"); crew(proj, "review", "t2", "--by", "dsh"); crew(proj, "review", "t2", "--by", "codex")
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("review", "single"))
    code, out = crew(proj, "gate", "t2")
    assert code == 1 and "crew.json" in out


def test_04_timeout(tmp_path):
    p = make_project(tmp_path / "p", timeouts={"dsh_minutes": 0.03, "codex_minutes": 1, "verify_minutes": 2})
    pid = tmp_path / "c.pid"
    crew(p, "snapshot", "t1")
    t0 = time.monotonic()
    code, _ = crew(p, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "sleep", "FAKE_PIDFILE": str(pid)})
    assert code == 2 and time.monotonic() - t0 < 30
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)
    assert crew(p, "gate", "t1")[0] == 2


@pytest.mark.parametrize("mode", ["empty", "garbage"])
def test_05_06_empty_or_broken_report(proj, mode):
    crew(proj, "snapshot", "t1")
    assert crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": mode})[0] == 2
    assert crew(proj, "gate", "t1")[0] != 0


def test_07_double_missing_codex(proj):
    crew(proj, "snapshot", "t2"); crew(proj, "review", "t2", "--by", "dsh")
    assert crew(proj, "gate", "t2")[0] == 2


def test_08_old_round_pass_not_used(proj):
    crew(proj, "snapshot", "t1"); crew(proj, "review", "t1", "--by", "dsh")
    (proj / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    crew(proj, "snapshot", "t1")
    code, out = crew(proj, "gate", "t1")
    assert code == 2 and "round-02" in out


def test_09_concurrent_dispatch_refused(proj):
    with hold_lock(proj / "crew" / "t1"):
        code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "另一个调度进程" in out
    assert crew(proj, "snapshot", "t1")[0] == 0   # 持锁进程退出后可正常进行


@pytest.mark.parametrize("kind", ["file", "dir", "broken"])
def test_11_symlinks(proj, kind):
    target = {"file": proj / "calc.py", "dir": proj / "crew", "broken": proj / "nope.py"}[kind]
    os.symlink(target, proj / ("alias.py" if kind != "dir" else "linked"))
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "符号链接" in out


def test_13_handoff_edit_invalidates(proj):
    crew(proj, "snapshot", "t1"); crew(proj, "review", "t1", "--by", "dsh")
    handoff(proj, "t1", "补一句\n")
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "handoff" in out


def test_14_downgrade_without_approval(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "review", "t2", "--by", "codex")
    crew(proj, "gate", "t2")
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("review", "single"))
    handoff(proj, "t2", "dsh:R01:B1 已改\n")
    code, out = crew(proj, "snapshot", "t2")
    assert code == 1 and "审查级别" in out


def test_15_prior_issue_omitted(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    handoff(proj, "t1", "dsh:R01:B1 已改\n")
    crew(proj, "snapshot", "t1")
    code, out = crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "omit_prior"})
    assert code == 2 and "漏表态" in out
    assert iss(proj, "t1")["dsh:R01:B1"]["status"] == "open"
    assert crew(proj, "gate", "t1")[0] != 0


def test_16_close_foreign_issue(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "review", "t2", "--by", "codex")
    handoff(proj, "t2", "dsh:R01:B1 已改\n")
    crew(proj, "snapshot", "t2")
    code, out = crew(proj, "review", "t2", "--by", "codex",
                     env_extra={"FAKE_MODE": "close_foreign", "FAKE_FOREIGN_ID": "dsh:R01:B1"})
    assert code == 2 and "无权" in out
    assert iss(proj, "t2")["dsh:R01:B1"]["status"] == "open"


def test_17_self_reported_pass_but_verify_failed(tmp_path):
    p = make_project(tmp_path / "p", verify=["exit 1"])
    crew(p, "snapshot", "t1"); crew(p, "review", "t1", "--by", "dsh")
    code, out = crew(p, "gate", "t1")
    assert code == 1 and "未通过" in out


def test_17b_verify_record_deleted(proj):
    crew(proj, "snapshot", "t1"); crew(proj, "review", "t1", "--by", "dsh")
    (proj / "crew" / "t1" / "round-01" / "verify" / "01" / "result.json").unlink()
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "缺少执行记录" in out


def test_18_verify_writes_scanned_file(tmp_path):
    p = make_project(tmp_path / "p", verify=["echo '# x' >> calc.py"])
    crew(p, "snapshot", "t1"); crew(p, "review", "t1", "--by", "dsh")
    code, out = crew(p, "gate", "t1")
    assert code == 1 and "验证命令写入" in out


def test_19_requirements_change(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    (proj / "crew" / "t1" / "requirements.md").write_text("add 可以返回任何值。\n", encoding="utf-8")
    handoff(proj, "t1", "dsh:R01:B1 不改 —— 需求已放宽\n")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "任务要求原文" in out
    assert crew(proj, "approve", "t1", "--kind", "change", "--reason", "用户确认需求变更")[0] == 0
    assert crew(proj, "snapshot", "t1")[0] == 0
    base = read_json(proj / "crew" / "t1" / "baseline.json")
    assert base["approvals"][-1]["before"]["requirements_text"].startswith("add(a, b)")


def test_20_close_then_revert_fix(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    handoff(proj, "t1", "dsh:R01:B1 已改 —— calc.py:2\n")
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")
    assert iss(proj, "t1")["dsh:R01:B1"]["status"] == "closing"
    (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    assert crew(proj, "gate", "t1")[0] == 1
    assert iss(proj, "t1")["dsh:R01:B1"]["status"] == "open"
    crew(proj, "approve", "t1", "--kind", "extra_round", "--reason", "用户同意再审")
    (proj / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    crew(proj, "snapshot", "t1")
    prompt = (proj / "crew" / "t1" / "round-03" / "frozen" / "prompt-dsh.md").read_text(encoding="utf-8")
    assert "dsh:R01:B1" in prompt


def test_21_attempts_archived_and_no_redraw(tmp_path):
    p = make_project(tmp_path / "p", timeouts={"dsh_minutes": 0.03, "codex_minutes": 1, "verify_minutes": 2})
    crew(p, "snapshot", "t1")
    crew(p, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "sleep"})
    assert crew(p, "review", "t1", "--by", "dsh")[0] == 0
    rv = p / "crew" / "t1" / "round-01" / "dsh"
    assert (rv / "attempt-01" / "meta.json").exists() and (rv / "attempt-02" / "verdict.json").exists()
    assert read_json(rv / "adopted.json")["attempt"] == 2
    code, out = crew(p, "review", "t1", "--by", "dsh")
    assert code == 1 and "不能同轮重跑" in out
    assert crew(p, "gate", "t1")[0] == 0
