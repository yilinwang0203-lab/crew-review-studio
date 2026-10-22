"""跨轮基线（spec §6）：首轮锁定，之后只能收紧；放宽和追加轮次须有用户批准。"""
from __future__ import annotations

from pathlib import Path

from .common import CrewError, crash_invalidated, now, read_json, round_dir, rounds, sha256_text, write_json

FILE = "baseline.json"
LEVEL = {"single": 1, "double": 2}
KEYS = ("review", "verify", "scan", "max_rounds", "requirements_sha", "requirements_text")


def current(rules: dict, req_text: str) -> dict:
    return {"review": rules["review"], "verify": rules["verify"], "scan": rules["scan"],
            "max_rounds": rules["max_rounds"], "requirements_sha": sha256_text(req_text),
            "requirements_text": req_text}


def load(td):
    p = Path(td) / FILE
    return read_json(p) if p.exists() else None


def save(td, base: dict) -> None:
    write_json(Path(td) / FILE, base)


def loosenings(base: dict, cur: dict) -> list:
    out = []
    if LEVEL[cur["review"]] < LEVEL[base["review"]]:
        out.append(f"审查级别从 {base['review']} 降为 {cur['review']}")
    for v in base["verify"]:
        if v not in cur["verify"]:
            out.append(f"删除或修改了必做验证：{v['cmd']}")
    for p in base["scan"]["include"]:
        if p not in cur["scan"]["include"]:
            out.append(f"扫描范围删除了 include：{p}")
    for p in cur["scan"]["exclude"]:
        if p not in base["scan"]["exclude"]:
            out.append(f"扫描范围新增了 exclude：{p}")
    if cur["max_rounds"] > base["max_rounds"]:
        out.append(f"max_rounds 从 {base['max_rounds']} 提高到 {cur['max_rounds']}")
    if cur["requirements_sha"] != base["requirements_sha"]:
        out.append("任务要求原文已改变")
    return out


def round_limit(base: dict) -> int:
    extra = sum(1 for a in base["approvals"] if a["kind"] == "extra_round" and a["applied"])
    return base["max_rounds"] + extra


def counted_rounds(td, upto: int = None) -> int:
    """计入轮次配额的轮数（到 upto 为止）：调度器中途崩溃作废的轮次不算。"""
    return sum(1 for r in rounds(td) if (upto is None or r <= upto) and not crash_invalidated(round_dir(td, r)))


def _pending(base: dict, kind: str):
    for a in reversed(base["approvals"]):
        if a["kind"] == kind and not a["applied"]:
            return a
    return None


def check_and_update(td, rules: dict, req_text: str, next_round: int) -> dict:
    cur = current(rules, req_text)
    base = load(td)
    if base is None:
        if next_round > 1:
            raise CrewError(f"基线 baseline.json 缺失，但该任务已有 {next_round - 1} 轮送审记录；"
                            "不能当作首轮重建基线，需人工处理（由用户裁决如何恢复）")
        return {**cur, "approvals": [], "history": [{"at": now(), "event": "created"}]}
    loose = loosenings(base, cur)
    if loose:
        appr = _pending(base, "change")
        if appr is None or appr["after"] != cur:
            raise CrewError("相对基线有放宽，需要用户批准后才能开新一轮：\n- " + "\n- ".join(loose)
                            + "\n用户同意后运行：crew.py approve <任务> --kind change --reason \"…\"")
        appr["applied"], appr["applied_at"] = True, now()
    used = counted_rounds(td)
    if used + 1 > round_limit(base):
        appr = _pending(base, "extra_round")
        if appr is None:
            raise CrewError(f"已送审 {next_round - 1} 轮（计入配额 {used} 轮），达到上限 {round_limit(base)}，需要用户裁决。"
                            "用户同意追加一轮后运行：crew.py approve <任务> --kind extra_round --reason \"…\"")
        appr["applied"], appr["applied_at"] = True, now()
    if any(base[k] != cur[k] for k in KEYS):
        base["history"].append({"at": now(), "event": "updated", "before": {k: base[k] for k in KEYS}})
        base.update(cur)
    return base


def approve(td, rules: dict, req_text: str, kind: str, reason: str) -> dict:
    base = load(td)
    if base is None:
        raise CrewError("该任务还没有基线（还没送审过），不需要批准")
    if kind not in ("change", "extra_round"):
        raise CrewError("kind 只能是 change 或 extra_round")
    if not reason.strip():
        raise CrewError("必须写批准理由")
    entry = {"at": now(), "kind": kind, "reason": reason, "applied": False}
    if kind == "change":
        cur = current(rules, req_text)
        loose = loosenings(base, cur)
        if not loose:
            raise CrewError("当前配置相对基线没有放宽，不需要批准")
        entry.update({"before": {k: base[k] for k in KEYS}, "after": cur, "items": loose})
    base["approvals"].append(entry)
    save(td, base)
    return entry
