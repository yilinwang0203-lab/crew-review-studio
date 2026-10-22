"""审查证据：dsh 事件流解析、被采用 attempt 的完整性复核（gate 与台账重放共用）。"""
from __future__ import annotations

import json
from pathlib import Path

from .common import CrewError, read_json
from .verdict import extract_json, normalize, severity_of, summary_of, validate

WRITE_TOOLS = {"write", "edit", "multiedit", "apply_patch", "patch", "delete", "remove", "move", "rename", "mkdir"}


def dsh_scan(events_path):
    calls, final, violations = {}, "", []
    for line in Path(events_path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "tool_call":
            calls[e.get("callId")] = e
        elif e.get("type") == "tool_result" and e.get("status") == "completed":
            c = calls.get(e.get("callId")) or {}
            tool = str(c.get("tool", "")).lower()
            inp = c.get("input") or {}
            if tool in WRITE_TOOLS:
                violations.append(f"成功执行了写类工具 {tool}：{json.dumps(inp, ensure_ascii=False)[:200]}")
            elif isinstance(inp, dict) and "sandbox_permissions" in inp:
                violations.append(f"带 sandbox_permissions 的调用成功执行（{tool}）")
        elif e.get("type") == "final":
            final = e.get("text") or ""
    return final, violations


REQUIRED_EVIDENCE = ("final.txt", "events.jsonl", "stderr.log", "exit_code", "meta.json", "verdict.json",
                     "review.md")   # stderr.log 可以为空，但必须存在


def raw_final(ad) -> str:
    """一个 attempt 里审查员已经给出的原始结论（任一来源非空即算）：final.txt、dsh 事件流的 final、Codex last-message。"""
    ad = Path(ad)
    for name in ("final.txt", "last-message.txt"):
        p = ad / name
        if p.is_file() and p.read_text(encoding="utf-8", errors="replace").strip():
            return p.read_text(encoding="utf-8", errors="replace")
    ev = ad / "events.jsonl"
    if ev.is_file():
        final, _ = dsh_scan(ev)
        if final.strip():
            return final
    return ""


def adopted_problems(rd, rev, task, n, fp):
    """完整复核被采用的 attempt：adopted、meta、退出状态、结论 JSON 互相一致且合格。

    返回 (问题列表, 合格时的结论 dict 否则 None, attempt 编号)。
    """
    rv = rd / rev
    try:
        a = read_json(rv / "adopted.json")
        k = a["attempt"]
        ad = rv / f"attempt-{k:02d}"
        meta = read_json(ad / "meta.json")
        v = read_json(ad / "verdict.json")
        exit_code = (ad / "exit_code").read_text(encoding="utf-8").strip()
        open_ids = [i["id"] for i in read_json(rd / "frozen" / f"open-issues-{rev}.json")]
    except (CrewError, OSError, KeyError, TypeError, ValueError) as e:
        return [f"{rev} 的采用记录不完整或损坏：{e}"], None, None
    out = []
    for name in REQUIRED_EVIDENCE + (("last-message.txt",) if rev == "codex" else ()):
        if not (ad / name).is_file():
            out.append(f"{rev} 的采用 attempt 缺少原始记录 {name}")
    if out:
        return out, None, k
    final = (ad / "final.txt").read_text(encoding="utf-8")
    if not final.strip():
        out.append(f"{rev} 的原始结论 final.txt 为空")
    else:
        raw = extract_json(final)
        normalize(raw)   # 采用时做过同样的确定性规整（格式笔误），比对规整后的结果
        if raw != v:
            out.append(f"{rev} 的原始结论 final.txt 与采用的 verdict.json 不一致")
    if rev == "dsh":
        ev_final, ev_viol = dsh_scan(ad / "events.jsonl")
        if ev_final != final:
            out.append("dsh 事件日志里的最终答案与 final.txt 不一致")
        if ev_viol:
            out.append("dsh 事件日志里有成功的写入/升权操作")
    elif (ad / "last-message.txt").read_text(encoding="utf-8") != final:
        out.append("Codex 的 last-message.txt 与 final.txt 不一致")
    if meta.get("reviewer") != rev or meta.get("attempt") != k:
        out.append(f"{rev} 的 meta.json 与 adopted.json 指向不一致")
    if meta.get("outcome") != a.get("verdict"):
        out.append(f"{rev} 的 meta 结论（{meta.get('outcome')}）与 adopted（{a.get('verdict')}）不一致")
    if meta.get("errors") or meta.get("violations") or meta.get("timed_out"):
        out.append(f"{rev} 的采用 attempt 记录了错误、违规或超时")
    if meta.get("exit_code") != 0 or exit_code != "0":
        out.append(f"{rev} 的采用 attempt 退出码不是 0")
    errs = validate(v, task, n, fp, open_ids, severity=severity_of(rd), summary=summary_of(rd))
    if errs:
        out.append(f"{rev} 的采用结论校验不通过：" + "；".join(errs))
    elif v["verdict"] != a.get("verdict") or v["verdict"] not in ("pass", "reject"):
        out.append(f"{rev} 的结论（{v['verdict']}）与 adopted（{a.get('verdict')}）不一致")
    return out, (None if out else v), k
