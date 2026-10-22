"""decide：用户在「需要用户裁决」后接受现状（只记录用户决定，不伪造审查放行）。"""
from __future__ import annotations

from pathlib import Path

from . import accepted_risks, gate, issues
from .common import CrewError, Lock, crash_point, latest_round, now, read_json, round_dir, task_dir, write_json


def accept(project, task: str, reason: str) -> dict:
    if not reason.strip():
        raise CrewError("必须写裁决理由（--reason）")
    project = Path(project).resolve()
    td = task_dir(project, task)
    with Lock(td):
        n = latest_round(td)
        if n is None:
            raise CrewError("还没有送审轮次，不需要裁决")
        gp = round_dir(td, n) / "gate.json"
        last = read_json(gp)["status"] if gp.exists() else "未 gate"
        if last == "needs_user":
            last = gate._gate_locked(project, task, td)["status"]   # 按当前文件重新确认，旧报告不能授权
        if last != "needs_user":
            raise CrewError(f"round-{n:02d} 当前状态是「{gate.LABEL.get(last, last)}」，"
                            "只有 gate 结果为「需要用户裁决」时才能 decide")
        unclosed = [{"id": i["id"], "text": i["text"]} for i in issues.load(td)["issues"] if i["status"] != "closed"]
        d = {"round": n, "decision": "accept", "at": now(), "by": "用户", "reason": reason.strip(),
             "accepted_issues": unclosed}
        write_json(td / "user_decision.json", d)   # 裁决的提交点；风险清单由它推出，可重复补齐
        crash_point("after_decision")
        accepted_risks.sync(project)
        return d
