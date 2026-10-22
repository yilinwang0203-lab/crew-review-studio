"""dsh 与 Codex 并行审查同一任务（每个审查员一把锁，任务锁只在准备与落定时短暂持有）。"""
import os
import shlex
import subprocess
import sys
import time

from conftest import CREW, FAKE, crew

from crewlib.common import task_dir


def _popen(project, *args, env_extra=None):
    fake = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    env = dict(os.environ, CREW_DSH_BIN=fake, CREW_CODEX_BIN=fake, DEEPSEEK_API_KEY="fake-key")
    env.update(env_extra or {})
    return subprocess.Popen([sys.executable, str(CREW), "--project", str(project), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)


def test_dsh_and_codex_run_in_parallel_then_release(proj):
    assert crew(proj, "snapshot", "t2")[0] == 0
    t0 = time.monotonic()
    a = _popen(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_DELAY": "3"})
    b = _popen(proj, "review", "t2", "--by", "codex", env_extra={"FAKE_DELAY": "3"})
    out_a, out_b = a.communicate(timeout=60)[0], b.communicate(timeout=60)[0]
    elapsed = time.monotonic() - t0
    assert a.returncode == 0 and b.returncode == 0, out_a + out_b
    assert "pass" in out_a and "pass" in out_b
    assert elapsed < 5.8, f"两位审查员应并行（各 3 秒），实际 {elapsed:.1f} 秒"
    code, out = crew(proj, "gate", "t2")
    assert code == 0 and "放行" in out, out


def test_same_reviewer_twice_refused(proj):
    assert crew(proj, "snapshot", "t2")[0] == 0
    a = _popen(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_DELAY": "3"})
    time.sleep(1.0)
    code, out = crew(proj, "review", "t2", "--by", "dsh")
    a.communicate(timeout=60)
    assert code != 0 and "dsh 已经在审查这个任务" in out, out


def test_gate_and_snapshot_while_other_reviewer_running(proj):
    assert crew(proj, "snapshot", "t2")[0] == 0
    a = _popen(proj, "review", "t2", "--by", "codex", env_extra={"FAKE_DELAY": "4"})
    time.sleep(1.0)
    code, out = crew(proj, "gate", "t2")                     # 正在跑的 Codex 不能被当成崩溃
    assert "等待" in out and not (task_dir(proj, "t2") / "round-01" / "INVALID").exists(), out
    code, out = crew(proj, "snapshot", "t2")                 # 有审查员在跑时不开新一轮
    assert code != 0 and "还在审查" in out, out
    assert a.communicate(timeout=60)[0] and a.returncode == 0
    assert crew(proj, "review", "t2", "--by", "dsh")[0] == 0
    code, out = crew(proj, "gate", "t2")
    assert code == 0 and "放行" in out, out


def test_scheduler_killed_mid_review_still_invalidates(proj):
    """调度器被杀（审查员锁随之释放）：其他命令照旧判本轮作废，并行改造不放过真正的崩溃。"""
    assert crew(proj, "snapshot", "t2")[0] == 0
    a = _popen(proj, "review", "t2", "--by", "dsh", env_extra={"FAKE_MODE": "sleep"})
    pid_file = task_dir(proj, "t2") / "round-01" / "dsh" / "attempt-01" / "pid"
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.1)
    a.kill()
    a.wait()
    code, out = crew(proj, "gate", "t2")
    assert (task_dir(proj, "t2") / "round-01" / "INVALID").exists(), out
