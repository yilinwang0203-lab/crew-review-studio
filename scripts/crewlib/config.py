"""读取项目配置 crew/crew.json（spec §4）。"""
from __future__ import annotations

import json
from pathlib import Path

from .common import CrewError

DEFAULT_TIMEOUTS = {"dsh_minutes": 15, "codex_minutes": 20, "verify_minutes": 10}
REVIEWERS = {"single": ["dsh"], "double": ["dsh", "codex"]}


def load(project) -> dict:
    p = Path(project) / "crew" / "crew.json"
    if not p.exists():
        raise CrewError(f"缺少项目配置 {p}")
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CrewError(f"crew.json 不是合法 JSON：{e}")
    scan = cfg.get("scan") or {}
    if not isinstance(scan.get("include"), list) or not scan["include"]:
        raise CrewError("crew.json 的 scan.include 必须是非空列表")
    if not isinstance(scan.get("exclude"), list) or "crew/**" not in scan["exclude"]:
        raise CrewError('crew.json 的 scan.exclude 必须是列表，且包含 "crew/**"')
    if scan.get("symlinks", "reject") != "reject":
        raise CrewError('第一版只支持 scan.symlinks = "reject"')
    if not isinstance(cfg.get("tasks"), dict):
        raise CrewError("crew.json 缺少 tasks")
    mr = cfg.get("max_rounds", 3)
    if not isinstance(mr, int) or isinstance(mr, bool) or mr < 1:
        raise CrewError("crew.json 的 max_rounds 必须是 ≥1 的整数")
    return cfg


def _verify_entry(v) -> dict:
    if isinstance(v, str):
        v = {"cmd": v}
    if not isinstance(v, dict) or not isinstance(v.get("cmd"), str) or not v["cmd"].strip():
        raise CrewError(f"verify 条目格式不对：{v!r}")
    return {"cmd": v["cmd"], "expect": v.get("expect"), "timeout_minutes": v.get("timeout_minutes")}


def task_rules(cfg: dict, task: str) -> dict:
    t = cfg["tasks"].get(task)
    if t is None:
        raise CrewError(f"crew.json 的 tasks 里没有 {task}")
    review = t.get("review")
    if review == "draft":
        raise CrewError(f"{task} 是设计阶段（draft）任务：Codex 写、Claude 修订、看板公示，不走审查")
    if review not in REVIEWERS:
        raise CrewError(f"{task}.review 只能是 single 或 double")
    req = t.get("requirements")
    if not isinstance(req, str) or not req:
        raise CrewError(f"{task}.requirements 必须写任务要求文件的路径")
    timeouts = dict(DEFAULT_TIMEOUTS)
    timeouts.update(cfg.get("timeouts") or {})
    return {
        "task": task,
        "review": review,
        "reviewers": list(REVIEWERS[review]),
        "verify": [_verify_entry(v) for v in t.get("verify", [])],
        "requirements": req,
        "scan": {"include": list(cfg["scan"]["include"]), "exclude": list(cfg["scan"]["exclude"]),
                 "symlinks": "reject"},
        "timeouts": timeouts,
        "max_rounds": cfg.get("max_rounds", 3),
    }


def read_requirements(project, rules: dict) -> str:
    p = Path(project) / rules["requirements"]
    if not p.is_file():
        raise CrewError(f"找不到任务要求文件 {p}")
    return p.read_text(encoding="utf-8")
