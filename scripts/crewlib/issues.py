"""跨轮问题台账 issues.json（spec §7）：open → closing → closed，脚本维护。"""
from __future__ import annotations

from pathlib import Path

from . import evidence
from .common import MAX_ATTEMPTS, CrewError, counted_attempts, now, read_json, round_dir, rounds, write_json

FILE = "issues.json"


def load(td) -> dict:
    p = Path(td) / FILE
    return read_json(p) if p.exists() else {"issues": []}


def save(td, data: dict) -> None:
    write_json(Path(td) / FILE, data)


def _hist(it: dict, at=None, **kw) -> None:
    it["history"].append({"at": at or now(), **kw})


def _move(it: dict, to: str, **kw) -> None:
    _hist(it, event="status", frm=it["status"], to=to, **kw)
    it["status"] = to


def open_ids(data: dict, reviewer=None) -> list:
    return [i["id"] for i in data["issues"]
            if i["status"] == "open" and (reviewer is None or i["raised_by"] == reviewer)]


def open_for(data: dict, reviewer: str) -> list:
    return [i for i in data["issues"] if i["status"] == "open" and i["raised_by"] == reviewer]


def record_replies(data: dict, handoff_text: str, round_no: int) -> None:
    for it in data["issues"]:
        if it["status"] == "open":
            lines = [l.strip() for l in handoff_text.splitlines() if it["id"] in l]
            it["replies"].append({"round": round_no, "lines": lines})


def apply_verdict(data: dict, reviewer: str, round_no: int, attempt: int, v: dict, fp: str, at=None) -> None:
    idx = {i["id"]: i for i in data["issues"]}
    for p in v["prior_issues"]:
        it = idx.get(p["id"])
        if it is None or it["raised_by"] != reviewer:
            continue
        if p["decision"] == "close":
            it["closing"] = {"round": round_no, "attempt": attempt, "reason": p["reason"], "fingerprint": fp}
            _move(it, "closing", at=at, by=reviewer, round=round_no, attempt=attempt, reason=p["reason"])
        else:
            _hist(it, at=at, event="keep_open", by=reviewer, round=round_no, attempt=attempt, reason=p["reason"])
    for b in v["blockers"]:
        gid = f"{reviewer}:R{round_no:02d}:{b['id']}"
        if gid in idx:
            continue
        it = {"id": gid, "raised_by": reviewer, "round": round_no, "attempt": attempt, "text": b,
              "replies": [], "status": "open", "history": []}
        _hist(it, at=at, event="raised", by=reviewer, round=round_no, attempt=attempt, to="open")
        data["issues"].append(it)
        idx[gid] = it


def revert_closing(data: dict, reason: str) -> None:
    for it in data["issues"]:
        if it["status"] == "closing":
            _move(it, "open", reason=reason)
            it.pop("closing", None)


def confirm_closing(data: dict, round_no: int, at=None) -> None:
    for it in data["issues"]:
        if it["status"] == "closing":
            _move(it, "closed", at=at, round=round_no)


def rebuild(td) -> dict:
    """从不可变记录重放出台账（崩溃一致性的关键）。

    来源只有三类，都是"写完即不再改"的单文件提交点：
      - round-NN/frozen/handoff.md（实现者的回复）
      - round-NN/<审查员>/adopted.json + 对应 attempt 的 verdict.json（审查结论的提交点）
      - round-NN/released.json（放行的提交点）、round-NN/INVALID（失效的提交点）
    issues.json 只是重放结果的缓存，任何命令开始时都会重算覆盖，所以中途崩溃不会丢 blocker，
    也不会留下没放行就 closed 的问题；重复重放结果相同。
    """
    data = {"issues": []}
    rs = rounds(td)
    for idx, r in enumerate(rs):
        rd = round_dir(td, r)
        if idx > 0 and not (round_dir(td, rs[idx - 1]) / "released.json").exists():
            revert_closing(data, f"round-{rs[idx - 1]:02d} 未放行")
        hp = rd / "frozen" / "handoff.md"
        if hp.exists():
            record_replies(data, hp.read_text(encoding="utf-8"), r)
        fpf = rd / "fingerprint.txt"
        fp = fpf.read_text(encoding="utf-8").strip() if fpf.exists() else ""
        for rev in ("dsh", "codex"):
            ap = rd / rev / "adopted.json"
            if not ap.exists():
                continue
            try:
                task = read_json(rd / "frozen" / "rules.json")["task"]
                probs, v, k = evidence.adopted_problems(rd, rev, task, r, fp)
                a = read_json(ap)
            except (CrewError, OSError, ValueError, KeyError, TypeError) as e:
                probs = [f"{type(e).__name__}: {e}"]
            if probs:
                # 历史采用记录缺失、损坏或与原始输出不一致时不能静默跳过（会丢 blocker），整个命令停下交人工处理
                raise CrewError(f"round-{r:02d} 的 {rev} 采用记录损坏，历史问题无法可靠重放；需人工处理：\n- "
                                + "\n- ".join(probs))
            apply_verdict(data, rev, r, k, v, fp, at=a.get("at"))
        if (rd / "released.json").exists():
            confirm_closing(data, r, at=read_json(rd / "released.json").get("at"))
        elif _dead(rd):
            revert_closing(data, f"round-{r:02d} 已不可能放行")
    return data


def _dead(rd) -> bool:
    """本轮已不可能放行：失效、有审查员采用了 reject、或有审查员跑满次数仍无结论。"""
    if (rd / "INVALID").exists():
        return True
    rp = rd / "frozen" / "rules.json"
    reviewers = read_json(rp)["reviewers"] if rp.exists() else ["dsh", "codex"]
    for rev in reviewers:
        ap = rd / rev / "adopted.json"
        if ap.exists():
            if read_json(ap).get("verdict") == "reject":
                return True
        elif counted_attempts(rd / rev) >= MAX_ATTEMPTS:
            return True
    return False
