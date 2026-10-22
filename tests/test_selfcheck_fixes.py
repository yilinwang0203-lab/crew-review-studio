"""selfcheck round-01 中 Codex 提出的 7 个 blocker 的回归测试（codex:R01:B1–B7）。"""
import json

from conftest import HANDOFF, crew, edit_cfg, make_project
from crewlib.common import read_json


def reply(proj, task, *ids):
    (proj / "crew" / task / "handoff.md").write_text(
        HANDOFF + "".join(f"{i} 已改\n" for i in ids), encoding="utf-8")


def status(proj, task):
    return {i["id"]: i["status"] for i in read_json(proj / "crew" / task / "issues.json")["issues"]}


def test_b1_missing_baseline_with_existing_rounds_refused(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    (proj / "crew" / "t2" / "baseline.json").unlink()          # 模拟保存基线前崩溃
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("review", "single"))
    reply(proj, "t2", "dsh:R01:B1")
    code, out = crew(proj, "snapshot", "t2")
    assert code == 1 and "基线" in out


def test_b2_completed_reject_before_adopted_is_recovered_not_redrawn(proj):
    crew(proj, "snapshot", "t1")
    code, _ = crew(proj, "review", "t1", "--by", "dsh",
                   env_extra={"FAKE_MODE": "reject", "CREW_CRASH_AT": "before_adopted"})
    assert code == -9
    assert not (proj / "crew" / "t1" / "round-01" / "dsh" / "adopted.json").exists()
    code, out = crew(proj, "review", "t1", "--by", "dsh")      # 不能重抽出 pass
    assert code == 1 and "不能同轮重跑" in out
    assert crew(proj, "gate", "t1")[0] == 1
    assert status(proj, "t1") == {"dsh:R01:B1": "open"}


def test_b2_attempt_without_meta_but_with_final_blocks_rerun(proj):
    crew(proj, "snapshot", "t1")
    code, _ = crew(proj, "review", "t1", "--by", "dsh",
                   env_extra={"FAKE_MODE": "reject", "CREW_CRASH_AT": "before_meta"})
    assert code == -9
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "中断" in out


def test_b3_corrupt_old_adopted_record_blocks_instead_of_dropping_issue(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    (proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01" / "verdict.json").write_text("{坏的")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "损坏" in out
    assert "dsh:R01:B1" in json.dumps(read_json(proj / "crew" / "t1" / "issues.json"), ensure_ascii=False)


def _released_ready(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")
    return proj / "crew" / "t1" / "round-01" / "verify" / "01"


def test_b4_verify_ok_flag_not_trusted(proj):
    v = _released_ready(proj)
    r = read_json(v / "result.json")
    r["exit_code"] = 1
    (v / "result.json").write_text(json.dumps(r))
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "退出码" in out


def test_b4_verify_output_log_required(proj):
    v = _released_ready(proj)
    (v / "output.log").unlink()
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "output.log" in out


def test_b4_verify_expect_rechecked_against_output(tmp_path):
    p = make_project(tmp_path / "p", verify=[{"cmd": "echo all passed", "expect": "passed"}])
    crew(p, "snapshot", "t1")
    crew(p, "review", "t1", "--by", "dsh")
    (p / "crew" / "t1" / "round-01" / "verify" / "01" / "output.log").write_text("1 failed\n")
    code, out = crew(p, "gate", "t1")
    assert code == 1 and "passed" in out


def test_b5_other_reviewer_reject_reverts_closing(proj):
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "review", "t2", "--by", "codex")
    crew(proj, "gate", "t2")
    reply(proj, "t2", "dsh:R01:B1")
    crew(proj, "snapshot", "t2")
    crew(proj, "review", "t2", "--by", "dsh")                              # 关闭旧问题
    crew(proj, "review", "t2", "--by", "codex", env_extra={"FAKE_MODE": "reject"})
    code, out = crew(proj, "gate", "t2")
    assert code == 3
    st = status(proj, "t2")
    assert st["dsh:R01:B1"] == "open" and "dsh:R01:B1" in out


def test_b6_extra_fields_rejected(proj):
    crew(proj, "snapshot", "t1")
    code, out = crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "extra_field"})
    assert code == 2 and "多余字段" in out


def test_b6_extra_nested_fields_normalized(proj):
    """2026-10-22 起：不拦放行的 polish/hardening 条目多余字段自动删除并留痕（原为判未完成）。"""
    crew(proj, "snapshot", "t1")
    code, out = crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "extra_nested"})
    assert code == 0 and "已自动规整" in out and "删除多余字段 extra" in out


def test_b7_partial_ledger_entry_repaired(proj):
    _released_ready(proj)
    assert crew(proj, "gate", "t1")[0] == 0
    ledger = proj / "crew" / "ledger.md"
    ledger.write_text("# crew 审查账本\n\n<!-- crew-release t1 round-01 -->\n", encoding="utf-8")   # 残缺记录
    assert crew(proj, "gate", "t1")[0] == 0
    text = ledger.read_text(encoding="utf-8")
    assert text.count("t1 round-01 放行") == 1 and "可加文档字符串" in text
    assert text.count("<!-- crew-release t1 round-01 -->") == 1


# ---- selfcheck round-02 的 3 类问题（dsh:R02:B1 = codex:R02:B1、codex:R02:B2、codex:R02:B3）----
import threading
import time

from conftest import hold_lock


def test_r2_crash_after_final_before_verdict_blocks(proj):
    crew(proj, "snapshot", "t1")
    code, _ = crew(proj, "review", "t1", "--by", "dsh",
                   env_extra={"FAKE_MODE": "reject", "CREW_CRASH_AT": "after_final"})
    assert code == -9
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "中断" in out
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "中断" in out


def test_r2_meta_settled_but_verdict_missing_blocks_everything(proj):
    """meta 记为 reject、verdict.json 缺失（旧写入顺序的崩溃窗口）：不能重抽，也不能开新一轮。"""
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    rv = proj / "crew" / "t1" / "round-01" / "dsh"
    (rv / "adopted.json").unlink()
    (rv / "attempt-01" / "verdict.json").unlink()
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "中断" in out
    reply(proj, "t1")
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "中断" in out


def test_r2_parsable_but_invalid_history_blocks(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    vp = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01" / "verdict.json"
    v = read_json(vp)
    v["blockers"] = []
    vp.write_text(json.dumps(v, ensure_ascii=False))
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "损坏" in out


def test_r2_history_verdict_mismatching_final_blocks(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    v = read_json(ad / "verdict.json")
    v["blockers"][0]["why"] = "被改过"
    (ad / "verdict.json").write_text(json.dumps(v, ensure_ascii=False))
    code, out = crew(proj, "snapshot", "t1")
    assert code == 1 and "损坏" in out


def test_r2_ledger_waits_for_project_lock(proj):
    from crewlib import ledger
    _released_ready(proj)
    assert crew(proj, "gate", "t1")[0] == 0
    (proj / "crew" / "ledger.md").unlink()
    done = []
    with hold_lock(proj / "crew"):
        t = threading.Thread(target=lambda: (ledger.ensure(proj, "t1", proj / "crew" / "t1" / "round-01", 1),
                                             done.append(1)))
        t.start()
        time.sleep(0.8)
        assert not done                       # 账本锁被占时必须等待，不能直接覆盖写
    t.join(10)
    assert done and "t1 round-01 放行" in (proj / "crew" / "ledger.md").read_text(encoding="utf-8")


def test_r2_two_tasks_release_concurrently_both_recorded(proj):
    import subprocess
    import sys
    from conftest import CREW, FAKE
    import os
    import shlex
    for t in ("t1", "t2"):
        crew(proj, "snapshot", t)
        crew(proj, "review", t, "--by", "dsh")
    crew(proj, "review", "t2", "--by", "codex")
    fake = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    env = dict(os.environ, CREW_DSH_BIN=fake, CREW_CODEX_BIN=fake, DEEPSEEK_API_KEY="k")
    ps = [subprocess.Popen([sys.executable, str(CREW), "--project", str(proj), "gate", t], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for t in ("t1", "t2")]
    assert [p.wait(60) for p in ps] == [0, 0]
    text = (proj / "crew" / "ledger.md").read_text(encoding="utf-8")
    assert "t1 round-01 放行" in text and "t2 round-01 放行" in text


# ---- selfcheck round-03：codex:R03:B1（原始结论已落在 events/last-message，final.txt 未写时崩溃）----
import pytest


@pytest.mark.parametrize("task,by", [("t1", "dsh"), ("t2", "codex")])
def test_r3_raw_output_saved_but_final_missing_blocks(proj, task, by):
    crew(proj, "snapshot", task)
    code, _ = crew(proj, "review", task, "--by", by,
                   env_extra={"FAKE_MODE": "reject", "CREW_CRASH_AT": "before_final"})
    assert code == -9
    ad = proj / "crew" / task / "round-01" / by / "attempt-01"
    assert not (ad / "final.txt").exists()
    code, out = crew(proj, "review", task, "--by", by)
    assert code == 1 and "中断" in out
    code, out = crew(proj, "snapshot", task)
    assert code == 1 and "中断" in out


def test_r3_crash_while_reviewer_running_without_final_invalidates_round(proj):
    """调度器中途崩溃、审查员还没给出结论：整轮失效，不同轮重跑（第 9 轮新规则）。"""
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n{"type": "thinking", "text": "..."}\n')
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "已失效" in out
    assert "中途崩溃" in (proj / "crew" / "t1" / "round-01" / "INVALID").read_text(encoding="utf-8")


# ---- selfcheck round-04：codex:R04:B1（中断 attempt 的越权证据）、codex:R04:B2（meta 记录版本）----
def test_r4_violation_in_interrupted_attempt_invalidates_round(proj):
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    rows = [{"type": "session"},
            {"type": "tool_call", "callId": "c", "tool": "write", "input": {"file_path": "/tmp/outside.txt"}},
            {"type": "tool_result", "callId": "c", "status": "completed", "result": "ok"}]
    (ad / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "已失效" in out
    assert "写入" in (proj / "crew" / "t1" / "round-01" / "INVALID").read_text(encoding="utf-8")
    assert crew(proj, "gate", "t1")[0] == 1


@pytest.mark.parametrize("task,by", [("t1", "dsh"), ("t2", "codex")])
def test_r4_meta_records_reviewer_version(proj, task, by):
    crew(proj, "snapshot", task)
    crew(proj, "review", task, "--by", by)
    meta = read_json(proj / "crew" / task / "round-01" / by / "attempt-01" / "meta.json")
    assert meta["version"] == "fake-reviewer 0.0.1"


# ---- selfcheck round-05：codex:R05:B1（孤儿审查进程）----
import os
import subprocess as _sp


def test_r5_orphan_reviewer_killed_before_rerun(proj):
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n')
    orphan = _sp.Popen(["sleep", "300"], start_new_session=True)     # 模拟仍在运行的旧审查员
    (ad / "pid").write_text(f"{orphan.pid} {_lstart(orphan.pid)}\n")
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "已失效" in out                              # 整轮失效，不同轮重跑
    assert orphan.wait(timeout=10) is not None                      # 旧审查员仍被尽力结束
    assert not (proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-02").exists()


def test_r5_unsettled_old_attempt_blocks_even_after_adoption(proj):
    crew(proj, "snapshot", "t1")
    crew(proj, "review", "t1", "--by", "dsh")                       # attempt-01 通过并采用
    late = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-02"
    late.mkdir()
    final = json.dumps({"verdict": "reject"})
    (late / "events.jsonl").write_text(json.dumps({"type": "session"}) + "\n"
                                       + json.dumps({"type": "final", "text": final}) + "\n")
    code, out = crew(proj, "gate", "t1")
    assert code == 1 and "中断" in out


# ---- selfcheck round-06：codex:R06:B1（Popen 之后、父进程写 pid 之前崩溃）----
def test_r6_pid_written_by_child_before_reviewer_runs(proj):
    crew(proj, "snapshot", "t1")
    code, _ = crew(proj, "review", "t1", "--by", "dsh",
                   env_extra={"FAKE_MODE": "sleep", "CREW_CRASH_AT": "after_spawn"})
    assert code == -9
    pf = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01" / "pid"
    for _ in range(40):
        if pf.exists() and pf.read_text().strip():
            break
        time.sleep(0.05)
    pgid = int(pf.read_text().split()[0])
    os.killpg(pgid, 0)                                             # 孤儿审查员确实还活着
    code, out = crew(proj, "review", "t1", "--by", "dsh")          # 整轮失效；孤儿被尽力结束
    assert code == 1 and "已失效" in out
    with pytest.raises((ProcessLookupError, PermissionError)):
        os.killpg(pgid, 0)



# ---- selfcheck round-07：codex:R07:B1（外壳写 pid 之前的窗口）、codex:R07:B2（pgid 复用误杀）----
def _lstart(pid):
    return " ".join(_sp.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout.split())


def _gone(pid):
    try:
        os.killpg(pid, 0)
    except (ProcessLookupError, PermissionError):
        return True
    return False


def test_r7_launcher_before_pid_write_found_by_command_line(proj):
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    # 外壳已启动、尚未写 pid：命令行里带着本 attempt 的 pid 文件路径
    shell = _sp.Popen(["/bin/sh", "-c", 'echo "$$ $(sleep 300)" > "$0" && exec "$@"', str(ad / "pid"), "true"],
                      start_new_session=True)
    bystander = _sp.Popen(["/bin/sh", "-c", f"sleep 300; : {ad / 'pid'}"], start_new_session=True)  # 只是命令行里含路径
    try:
        assert crew(proj, "review", "t1", "--by", "dsh")[0] == 1
        shell.wait(timeout=10)
        assert _gone(shell.pid)
        assert not _gone(bystander.pid)                               # 不是启动外壳，不能误杀
    finally:
        bystander.kill()
        bystander.wait()


def test_r7_reused_pgid_not_killed_and_handled_once(proj):
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    other = _sp.Popen(["sleep", "300"], start_new_session=True)       # 复用了旧号的无关进程
    (ad / "pid").write_text(f"{other.pid} Mon Jan  1 00:00:00 2001\n")  # 记录的启动时间对不上
    try:
        assert crew(proj, "review", "t1", "--by", "dsh")[0] == 1
        assert not _gone(other.pid)                                   # 身份不符，不能杀
        assert (ad / "orphan-handled").exists()
        crew(proj, "gate", "t1")
        assert not _gone(other.pid)
    finally:
        other.kill()
        other.wait()



# ---- 第 9 轮新规则：孤儿审查员事后交出结论，也影响不了任何放行 ----
def test_r9_late_output_in_invalidated_round_cannot_affect_release(proj):
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n')
    assert crew(proj, "review", "t1", "--by", "dsh")[0] == 1            # round-01 判失效
    late = json.dumps({"verdict": "reject"})                              # 孤儿事后才交出结论
    with open(ad / "events.jsonl", "a") as f:
        f.write(json.dumps({"type": "final", "text": late}) + "\n")
    assert crew(proj, "snapshot", "t1")[0] == 0                         # 新一轮照常开，不被死轮次卡住
    assert crew(proj, "review", "t1", "--by", "dsh")[0] == 0
    assert crew(proj, "gate", "t1")[0] == 0                             # 放行只依据 round-02 的全新审查
    assert "attempt-01" in (proj / "crew" / "t1" / "round-01" / "orphaned.json").read_text(encoding="utf-8")


def test_r9_unsettled_final_present_at_recovery_still_blocks(proj):
    """恢复时已经能看到未落定的结论：照旧交用户裁决，不能当作没发生。"""
    crew(proj, "snapshot", "t1")
    ad = proj / "crew" / "t1" / "round-01" / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n' + json.dumps({"type": "final", "text": "{}"}) + "\n")
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "中断" in out


def test_r9_crash_in_already_invalid_round_is_registered(proj):
    crew(proj, "snapshot", "t1")
    rd = proj / "crew" / "t1" / "round-01"
    (rd / "INVALID").write_text("送审后源码变化\n")                      # 本轮早已因别的原因作废
    ad = rd / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n')
    crew(proj, "gate", "t1")
    assert "dsh/attempt-01" in (rd / "orphaned.json").read_text(encoding="utf-8")
    with open(ad / "events.jsonl", "a") as f:
        f.write(json.dumps({"type": "final", "text": "{}"}) + "\n")       # 孤儿事后交出结论
    assert crew(proj, "snapshot", "t1")[0] == 0                          # 不会把后续命令卡死


# ---- selfcheck round-09：dsh:R09:B1（kill_group 未容忍 EPERM）、codex:R09:B1（登记与作废的先后）----
def test_r9b_kill_group_tolerates_eperm(monkeypatch):
    from crewlib import common

    class P:
        pid = 12345

        def wait(self, timeout=None):
            return 0

    def eperm(*a):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(common.os, "killpg", eperm)
    common.kill_group(P())          # macOS 僵尸组 killpg 返回 EPERM：不能让调度器崩溃


def test_r9b_orphan_registered_but_invalid_missing_is_completed(proj):
    crew(proj, "snapshot", "t1")
    rd = proj / "crew" / "t1" / "round-01"
    ad = rd / "dsh" / "attempt-01"
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n')
    (rd / "orphaned.json").write_text('["dsh/attempt-01"]')          # 模拟登记后、写 INVALID 前崩溃
    code, out = crew(proj, "review", "t1", "--by", "dsh")
    assert code == 1 and "已失效" in out
    assert (rd / "INVALID").exists()
