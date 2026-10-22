"""review：以只读、一次性、全新会话方式调用 dsh / Codex（spec §9、§11）。"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import external, issues, recover, verdict
from .evidence import dsh_scan
from .common import (MAX_ATTEMPTS, CrewError, Lock, counted_attempts, crash_point, invalidate, kill_group, latest_round,
                     next_attempt, now, read_json, reviewer_lock, round_dir, task_dir, write_json)
from .verdict import SCHEMA_PATH, extract_json, render_diagnostic, render_md, severity_of, summary_of, validate

CODEX_DEFAULT = "codex"
SCHEMA = SCHEMA_PATH
_sleep = time.sleep   # 外部故障退避等待（测试替换这里，不碰全局 time.sleep）


def deepseek_key() -> str:
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k
    svc = os.environ.get("CREW_KEYCHAIN_SERVICE", "crew-review-deepseek-api")
    security = shutil.which("security")
    if not security:
        raise CrewError("取不到 DeepSeek key：请设置环境变量 DEEPSEEK_API_KEY")
    r = subprocess.run([security, "find-generic-password", "-a", "deepseek", "-s", svc, "-w"],
                       capture_output=True, text=True)
    k = r.stdout.strip()
    if r.returncode != 0 or not k:
        raise CrewError(f"取不到 DeepSeek key：环境变量 DEEPSEEK_API_KEY 为空，钥匙串条目 {svc} 也读不到")
    return k


def _base(reviewer: str) -> list:
    if reviewer == "dsh":
        env_bin = os.environ.get("CREW_DSH_BIN")
        return shlex.split(env_bin) if env_bin else [shutil.which("dsh") or "dsh"]
    env_bin = os.environ.get("CREW_CODEX_BIN")
    return shlex.split(env_bin) if env_bin else [shutil.which("codex") or CODEX_DEFAULT]


def reviewer_version(reviewer: str) -> str:
    """记录当次使用的审查员命令行版本（spec §10 meta 要求）；取不到时如实记录原因，不阻断审查。"""
    try:
        r = subprocess.run(_base(reviewer) + ["--version"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"unknown（{type(e).__name__}）"
    lines = (r.stdout or r.stderr).strip().splitlines()
    return lines[0] if r.returncode == 0 and lines else f"unknown（退出码 {r.returncode}）"


def command(reviewer: str, project, ad, schema=None) -> list:
    base = _base(reviewer)
    if reviewer == "dsh":
        return base + ["--profile", "headless", "--json", "-"]
    return base + ["-a", "never", "exec", "-s", "read-only", "--ephemeral", "--skip-git-repo-check", "--json",
                   "--output-schema", str(schema or SCHEMA), "-o", str(Path(ad) / "last-message.txt"),
                   "-C", str(project), "-"]


def run(project, task: str, reviewer: str) -> dict:
    """三段式：①任务锁内准备 ②只持审查员锁运行审查员（dsh 与 Codex 可并行）③任务锁内落定结论。

    外部故障（容量不足/断网/限流）在同一次调用内按 60/120/240 秒退避重试，每次是新 attempt、不计次数；
    额度用完不重试。等待期间只持审查员锁，不挡另一位审查员和 gate。
    结论只因格式不合规而未完成时（自动规整后仍不合规），同一次调用内自动再审一次（计入次数上限）。
    """
    project = Path(project).resolve()
    td = task_dir(project, task)
    waits = external.backoff_seconds()
    with reviewer_lock(td, reviewer):
        retry, format_retry = 0, False
        while True:
            # 任务锁在这两段只持有很短时间：另一位审查员同时进出时排队等待，而不是报错退出
            with Lock(td, wait=True):
                ctx = _prepare(project, task, td, reviewer)
            _execute(project, reviewer, ctx)
            with Lock(td, wait=True):
                meta = _settle(td, reviewer, ctx)
            if (meta["outcome"] == "incomplete" and not format_retry and verdict.format_only(meta["errors"])
                    and counted_attempts(ctx["rv"]) < MAX_ATTEMPTS):
                format_retry = True
                print(f"{reviewer} attempt-{meta['attempt']:02d} 结论格式不合规（{meta['errors'][0]}），自动再审一次",
                      flush=True)
                continue
            if meta["outcome"] != "external" or not meta["external_retryable"] or retry >= len(waits):
                return meta
            print(f"{reviewer} attempt-{meta['attempt']:02d} 外部故障：{meta['external_reason']}；"
                  f"{waits[retry]:g} 秒后第 {retry + 1} 次重试", flush=True)
            _sleep(waits[retry])
            retry += 1


def _prepare(project, task, td, reviewer) -> dict:
    recover.recover(project, task, td, held=reviewer)
    n = latest_round(td)
    if n is None:
        raise CrewError("还没有送审轮次，先运行 snapshot")
    rd = round_dir(td, n)
    if (rd / "released.json").exists():
        raise CrewError(f"round-{n:02d} 已放行，不需要再审")
    if (rd / "INVALID").exists():
        raise CrewError(f"round-{n:02d} 已失效：{(rd / 'INVALID').read_text(encoding='utf-8').strip()}"
                        "；请修改后重新 snapshot")
    rules = read_json(rd / "frozen" / "rules.json")
    if reviewer not in rules["reviewers"]:
        raise CrewError(f"{task} 是 {rules['review']} 审查，不需要 {reviewer}")
    rv = rd / reviewer
    rv.mkdir(exist_ok=True)
    if (rv / "adopted.json").exists():
        a = read_json(rv / "adopted.json")
        raise CrewError(f"round-{n:02d} 的 {reviewer} 已有结论（attempt-{a['attempt']:02d}：{a['verdict']}），"
                        "不能同轮重跑替换")
    if counted_attempts(rv) >= MAX_ATTEMPTS:
        raise CrewError(f"round-{n:02d} 的 {reviewer} 已跑满 {MAX_ATTEMPTS} 次仍未完成，交用户裁决")
    env = dict(os.environ)
    if reviewer == "dsh":
        env["DSH_PERMISSION_MODE"] = "read-only"
        env["DEEPSEEK_API_KEY"] = deepseek_key()
    k = next_attempt(rv)
    ad = rv / f"attempt-{k:02d}"
    ad.mkdir()
    fs = rd / "frozen" / "verdict.schema.json"   # 用本轮冻结的结论格式
    return {"task": task, "n": n, "rd": rd, "rv": rv, "ad": ad, "k": k, "rules": rules, "env": env,
            "fp": (rd / "fingerprint.txt").read_text(encoding="utf-8").strip(),
            "cmd": command(reviewer, project, ad, fs if fs.exists() else SCHEMA)}


def _execute(project, reviewer, ctx) -> None:
    """运行审查员并把输出写进本 attempt 自己的目录（不碰轮次级文件，所以不需要任务锁）。"""
    rd, ad, cmd = ctx["rd"], ctx["ad"], ctx["cmd"]
    ctx["version"] = reviewer_version(reviewer)
    minutes = ctx["minutes"] = ctx["rules"]["timeouts"][f"{reviewer}_minutes"]
    ctx["started"], ctx["t0"] = now(), time.monotonic()
    code, timed_out, spawn_error = None, False, None
    with open(rd / "frozen" / f"prompt-{reviewer}.md", "rb") as fin, \
            open(ad / "events.jsonl", "wb") as fout, open(ad / "stderr.log", "wb") as ferr:
        # 由子进程自己先写 pid 再 exec 成审查员：只要审查员存在，pid 记录就一定已落盘，
        # 不依赖调度器在 Popen 之后还活着（否则崩溃窗口里会产生无记录的孤儿审查员）
        # pid 文件内容为"进程组号 启动时间"：恢复时据启动时间核对身份，避免进程号被复用后误杀无关进程
        launcher = ["/bin/sh", "-c", 'echo "$$ $(ps -o lstart= -p $$)" > "$0" && exec "$@"', str(ad / "pid")] + cmd
        try:
            p = subprocess.Popen(launcher, cwd=str(project), stdin=fin, stdout=fout, stderr=ferr, env=ctx["env"],
                                 start_new_session=True)
        except OSError as e:
            spawn_error = f"无法启动审查员：{e}"
        else:
            crash_point("after_spawn")
            try:
                code = p.wait(timeout=minutes * 60)
            except subprocess.TimeoutExpired:
                kill_group(p)
                timed_out = True
    (ad / "exit_code").write_text(f"{code}\n")
    if reviewer == "dsh":
        final, violations = dsh_scan(ad / "events.jsonl")
    else:
        lm = ad / "last-message.txt"
        final, violations = (lm.read_text(encoding="utf-8") if lm.exists() else ""), []
    crash_point("before_final")
    (ad / "final.txt").write_text(final, encoding="utf-8")   # 原始结论始终留档
    crash_point("after_final")
    ctx.update(code=code, timed_out=timed_out, spawn_error=spawn_error, final=final, violations=violations)


def _settle(td, reviewer, ctx) -> dict:
    """任务锁内：校验结论、写 meta（attempt 完成标记）、adopted（提交点）、重放台账。"""
    rd, rv, ad, k, n = ctx["rd"], ctx["rv"], ctx["ad"], ctx["k"], ctx["n"]
    final, violations, code, timed_out = ctx["final"], ctx["violations"], ctx["code"], ctx["timed_out"]
    open_ids = [i["id"] for i in read_json(rd / "frozen" / f"open-issues-{reviewer}.json")]
    errors, obj, normalized = [], None, []
    try:
        obj = extract_json(final)
        if ctx["spawn_error"]:
            errors.append(ctx["spawn_error"])
        elif timed_out:
            errors.append(f"超时（{ctx['minutes']} 分钟），已终止整个进程组")
        elif code != 0:
            errors.append(f"退出码 {code}")
        if violations:
            errors.append("审查员成功执行了写入/升权操作：" + "；".join(violations))
        if not final.strip():
            errors.append("没有输出最终结论")
        elif obj is None:
            errors.append("最终结论不是合法 JSON")
        else:
            normalized = verdict.normalize(obj)
            errors += validate(obj, ctx["task"], n, ctx["fp"], open_ids, severity=severity_of(rd), summary=summary_of(rd))
            if not errors and obj["verdict"] == "incomplete":
                errors.append("审查员自评未完成")
    except Exception as e:  # 任何意外都记为未完成，不让调度器崩溃
        errors.append(f"处理审查结论时出错：{type(e).__name__}: {e}")
    outcome = obj["verdict"] if not errors else "incomplete"
    ext = None
    if outcome == "incomplete" and not violations and (not final.strip() or code != 0):
        ext = external.classify(ad)   # 只在审查员没交出结论又异常退出时才认外部故障，不替换有效结论
        if ext:
            outcome = "external"
            errors.append(f"外部故障：{ext['reason']}（不计入 {MAX_ATTEMPTS} 次）")
    if outcome in ("pass", "reject"):
        write_json(ad / "verdict.json", obj)
        (ad / "review.md").write_text(render_md(obj, reviewer, k), encoding="utf-8")
    else:
        (ad / "review.md").write_text(render_diagnostic(obj, final, errors, reviewer, k), encoding="utf-8")
    meta = {"reviewer": reviewer, "attempt": k, "outcome": outcome, "errors": errors,
            "violations": violations, "exit_code": code, "timed_out": timed_out,
            "started": ctx["started"], "ended": now(), "seconds": round(time.monotonic() - ctx["t0"], 1),
            "command": ctx["cmd"], "version": ctx["version"], "normalized": normalized}
    if ext:
        meta.update(external_reason=ext["reason"], external_kind=ext["kind"], external_retryable=ext["retryable"])
    if violations:
        invalidate(rd, "other", f"{reviewer} attempt-{k:02d} 审查期间发生写入/升权：" + "；".join(violations))
    crash_point("before_meta")
    write_json(ad / "meta.json", meta)   # meta 是本次 attempt 的完成标记：结论文件都写好之后才写
    if outcome in ("pass", "reject"):
        # adopted.json 是审查结论的唯一提交点：它之前的文件都已写好；台账由重放得出
        crash_point("before_adopted")
        write_json(rv / "adopted.json", {"attempt": k, "verdict": outcome, "at": now()})
        crash_point("after_adopted")
        issues.save(td, issues.rebuild(td))
    return meta
