"""gate：按冻结规则判定是否放行（spec §11）。"""
from __future__ import annotations

import json
from pathlib import Path

from . import baseline, config, evidence, fingerprint, issues, ledger, recover, verify
from .common import (MAX_ATTEMPTS, CrewError, Lock, counted_attempts, crash_point, invalidate, latest_round, now, read_json,
                     round_dir, sha256_bytes, task_dir, write_json)

LABEL = {"released": "放行", "rejected": "退回", "invalid": "本轮失效", "pending": "等待审查",
         "needs_user": "需要用户裁决"}


def _frozen_problems(project, td, rd, rules) -> list:
    out = []
    fz = rd / "frozen"
    for name, h in read_json(fz / "hashes.json").items():
        p = fz / name
        if not p.exists() or sha256_bytes(p.read_bytes()) != h:
            out.append(f"冻结文件被改动：frozen/{name}")
    try:
        cur = config.task_rules(config.load(project), rules["task"])
        if cur != rules:
            out.append("crew/crew.json 中本任务规则或全局 scan/timeouts/max_rounds 已在送审后改变")
        if config.read_requirements(project, cur) != (fz / "requirements.md").read_text(encoding="utf-8"):
            out.append("任务要求原文已在送审后改变")
    except CrewError as e:
        out.append(f"读取当前配置失败：{e}")
    hp = td / "handoff.md"
    if not hp.exists() or hp.read_bytes() != (fz / "handoff.md").read_bytes():
        out.append("handoff.md 已在送审后改变")
    return out


def _manifest_problems(project, rd, rules, fp) -> list:
    old = fingerprint.parse_manifest((rd / "manifest.txt").read_text(encoding="utf-8"))
    if fingerprint.total(old) != fp:
        return ["manifest.txt 与 fingerprint.txt 不一致"]
    try:
        new = fingerprint.scan(project, rules["scan"]["include"], rules["scan"]["exclude"])
    except fingerprint.SymlinkError as e:
        return [str(e)]
    d = fingerprint.diff(old, new)
    return [] if fingerprint.is_clean(d) else ["送审后扫描范围内文件有变化：" + fingerprint.describe(d)]


def adopted_signature(rd, reviewers: list) -> dict:
    """本轮可判定状态的指纹：各审查员 adopted.json 内容与是否失效。与上次 gate 记录不同就说明结论有更新。"""
    sig = {}
    for rev in reviewers:
        ap = rd / rev / "adopted.json"
        sig[rev] = sha256_bytes(ap.read_bytes()) if ap.exists() else None
    sig["INVALID"] = (rd / "INVALID").exists()
    return sig


def gate(project, task: str) -> dict:
    project = Path(project).resolve()
    td = task_dir(project, task)
    with Lock(td):
        return _gate_locked(project, task, td)


def _gate_locked(project, task: str, td) -> dict:
    """调用方已持任务锁（gate 命令、snapshot 开轮前的自动 gate）。"""
    recover.recover(project, task, td)
    n = latest_round(td)
    if n is None:
        raise CrewError("还没有送审轮次，先运行 snapshot")
    rd = round_dir(td, n)
    released_before = (rd / "released.json").exists()
    rules = read_json(rd / "frozen" / "rules.json")
    fp = (rd / "fingerprint.txt").read_text(encoding="utf-8").strip()
    invalid = []
    if (rd / "INVALID").exists():
        invalid.append((rd / "INVALID").read_text(encoding="utf-8").strip())
    invalid += _frozen_problems(project, td, rd, rules)
    invalid += _manifest_problems(project, rd, rules, fp)
    invalid += verify.problems(rd, rules, fp)
    pending, exhausted, rejects, reviews = [], [], [], {}
    for rev in rules["reviewers"]:
        rv = rd / rev
        if (rv / "adopted.json").exists():
            probs, v, attempt = evidence.adopted_problems(rd, rev, rules["task"], n, fp)
            invalid += probs
            if v is not None:
                reviews[rev] = {"attempt": attempt, "verdict": v["verdict"]}
                if v["verdict"] != "pass":
                    rejects.append(rev)
        else:
            k = counted_attempts(rv) if rv.exists() else 0
            (exhausted if k >= MAX_ATTEMPTS else pending).append(rev)
    open_now = issues.open_ids(issues.load(td))   # recover 刚重放过
    base = baseline.load(td)
    if base is None:
        raise CrewError("基线 baseline.json 缺失，但该任务已有送审记录；不能判定轮次上限，需人工处理（由用户裁决如何恢复）")
    limit = baseline.round_limit(base)
    used = baseline.counted_rounds(td, n)
    if invalid:
        status = "invalid"
        invalidate(rd, "other", "；".join(invalid))   # 失效的提交点（已放行的轮次重放时以 released.json 为准）
        issues.save(td, issues.rebuild(td))
        open_now = issues.open_ids(issues.load(td))
    elif exhausted:
        status = "needs_user"
    elif pending:
        status = "pending"
    elif rejects or open_now:
        status = "needs_user" if used >= limit else "rejected"
    else:
        status = "released"
        if not released_before:
            crash_point("before_release_commit")
            # released.json 是放行的唯一提交点；台账和账本都由它推出，可重复补齐
            write_json(rd / "released.json", {"round": n, "fingerprint": fp, "reviews": reviews, "at": now()})
            crash_point("after_release_commit")
        issues.save(td, issues.rebuild(td))
        ledger.ensure(project, task, rd, n)
    report = {"task": task, "round": n, "status": status, "fingerprint": fp, "invalid": invalid,
              "pending": pending, "exhausted": exhausted, "rejects": rejects, "open_issues": open_now,
              "reviews": reviews, "counted_rounds": used, "round_limit": limit,
              "adopted_signature": adopted_signature(rd, rules["reviewers"]), "at": now()}
    write_json(rd / "gate.json", report)
    with open(rd / "gate-history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False) + "\n")
    return report


def describe(r: dict) -> str:
    lines = [f"{r['task']} round-{r['round']:02d}：{LABEL[r['status']]}"]
    lines += [f"- 失效原因：{x}" for x in r["invalid"]]
    lines += [f"- 等待审查员：{x}" for x in r["pending"]]
    lines += [f"- 审查员跑满 {MAX_ATTEMPTS} 次仍未完成：{x}" for x in r["exhausted"]]
    lines += [f"- 退回：{x}" for x in r["rejects"]]
    if r["open_issues"]:
        lines.append("- 未关闭问题：" + "、".join(r["open_issues"]))
    if r["status"] == "needs_user":
        lines.append("- 已达到轮次上限或审查员无法完成，请用户裁决")
    return "\n".join(lines)
