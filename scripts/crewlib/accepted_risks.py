"""项目级「已接受风险」清单 crew/accepted-risks.json：用户 decide 接受的问题，冻结进之后每轮审查指令。"""
from __future__ import annotations

import json
from pathlib import Path

from .common import CrewError, Lock, write_json

FILE = "accepted-risks.json"


def path(project) -> Path:
    return Path(project) / "crew" / FILE


def load(project) -> dict:
    """缺失 = 空清单；存在但格式不对就报错，不当成空清单覆盖。"""
    p = path(project)
    if not p.exists():
        return {"risks": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise CrewError(f"{p} 不是合法 JSON（{e}），需人工处理")
    if not isinstance(data, dict) or not isinstance(data.get("risks"), list):
        raise CrewError(f"{p} 格式不对：根节点必须是含 risks 列表的对象，需人工处理")
    for k, r in enumerate(data["risks"]):
        if not _valid(r):
            raise CrewError(f"{p} 第 {k + 1} 条风险格式不对（需要 id、text、reason、source.task、source.issue_id），需人工处理")
    return data


def _valid(r) -> bool:
    src = r.get("source") if isinstance(r, dict) else None
    return (isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("text"), dict)
            and isinstance(r.get("reason"), str) and isinstance(src, dict)
            and isinstance(src.get("task"), str) and isinstance(src.get("issue_id"), str))


def sync(project) -> dict:
    """把各任务已提交的用户裁决（user_decision.json 的 accepted_issues）补进清单，按 任务+问题 ID 去重。
    decide 写完裁决后、snapshot 冻结清单前都会调用，所以 decide 中途崩溃也会在下次补齐。"""
    crew_dir = Path(project) / "crew"
    with Lock(crew_dir, wait=True):   # 项目锁；调用方可能持有任务锁（任务锁 → 项目锁，与台账同序）
        data = load(project)
        seen = {r["source"]["task"] + ":" + r["source"]["issue_id"] for r in data["risks"]}
        added = 0
        for dp in sorted(crew_dir.glob("*/user_decision.json")):
            task = dp.parent.name
            try:
                d = json.loads(dp.read_text(encoding="utf-8"))
            except ValueError as e:
                raise CrewError(f"{dp} 不是合法 JSON（{e}），需人工处理")
            if not isinstance(d, dict) or d.get("decision") != "accept" or not isinstance(d.get("accepted_issues"), list):
                continue   # 旧版手写裁决没有问题原文，不反推
            for it in d["accepted_issues"]:
                if not (isinstance(it, dict) and isinstance(it.get("id"), str) and isinstance(it.get("text"), dict)):
                    raise CrewError(f"{dp} 的 accepted_issues 条目格式不对（需要 id 与 text 原文），需人工处理")
                key = f"{task}:{it['id']}"
                if key in seen:
                    continue
                seen.add(key)
                data["risks"].append({"id": key, "text": it["text"], "reason": d.get("reason", ""),
                                      "source": {"task": task, "issue_id": it["id"]}, "at": d.get("at")})
                added += 1
        if added:
            write_json(path(project), data)
        return data
