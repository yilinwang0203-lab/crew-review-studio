"""用模板生成审查指令（spec §7 附件）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .verdict import EXAMPLE

PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _issues_text(mine: list) -> str:
    if not mine:
        return "（无）"
    parts = []
    for i in mine:
        reply = "\n".join(i["replies"][-1]["lines"]) if i["replies"] else "（实现者未回复）"
        parts.append(f"### {i['id']}（第 {i['round']} 轮提出）\n问题原文：\n"
                     f"{json.dumps(i['text'], ensure_ascii=False, indent=2)}\n实现者回复：\n{reply}")
    return "\n\n".join(parts)


def risks_text(risks: list) -> str:
    if not risks:
        return "（无）"
    parts = ["以下风险用户已明确接受：不得作为 blocker 提出；确有新情况（例如影响范围扩大），只能记 polish，并说明新在哪。"]
    for r in risks:
        src = r["source"]
        parts.append(f"### {r['id']}（来源：任务 {src['task']} 的 {src['issue_id']}；用户理由：{r['reason']}）\n"
                     f"{json.dumps(r['text'], ensure_ascii=False, indent=2)}")
    return "\n\n".join(parts)


def scope_text(round_no: int, changes: str = None) -> str:
    """第 1 轮审全部；第 2 轮起只审上轮问题 + 本轮改动（收敛规则）。changes 为相对上一轮的文件变化描述。"""
    if round_no <= 1:
        return "第 1 轮：按任务要求审查全部改动。"
    head = (f"第 {round_no} 轮复审，只做两件事：①对上面每个未关闭问题逐条表态是否已解决；"
            "②审查本轮相对上一轮的改动。")
    body = (f"本轮改动的文件：{changes}。" if changes else
            "本轮相对上一轮没有改动扫描范围内的文件（只改了交接包/回复），只需对未关闭问题表态。")
    rule = ("没有改动的代码视为上一轮已审过：不再从中提出新的 blocker。只有本轮改动直接引入的问题，"
            "或本轮改动让原有代码出错，才算 blocker；其余新发现记 polish 或 hardening。")
    return "\n".join([head, body, rule])


def verify_text(task: str, round_no: int, verify: list) -> str:
    """必做验证在开轮时（审查开始前）已由流程在可写环境跑完：告诉审查员结果在哪，免得重跑（2026-10-22 提速）。"""
    if not verify:
        return "（本任务没有配置必做验证命令）"
    lines = ["开轮时流程已在可写环境里依次运行下列命令，审查开始前已全部结束。请先读结果文件"
             "（result.json 的 exit_code / ok / timed_out，output.log 为完整输出），不要重跑这些命令："]
    for i, v in enumerate(verify, 1):
        cmd = v["cmd"] if isinstance(v, dict) else str(v)
        base = f"crew/{task}/round-{round_no:02d}/verify/{i:02d}"
        lines.append(f"{i}. `{cmd}` → {base}/result.json、{base}/output.log")
    return "\n".join(lines)


def build(reviewer: str, task: str, round_no: int, fp: str, handoff: str, req: str, mine: list,
          scope: str = None, accepted_risks: list = None, verify: list = None) -> str:
    t = (PROMPT_DIR / f"{reviewer}.md").read_text(encoding="utf-8")
    pairs = [
        ("{{SCOPE}}", scope or scope_text(round_no)),
        ("{{VERIFY}}", verify_text(task, round_no, verify or [])),
        ("{{TASK}}", task), ("{{ROUND}}", str(round_no)), ("{{FINGERPRINT}}", fp),
        ("{{OPEN_ISSUE_IDS}}", ",".join(i["id"] for i in mine) or "无"),
        ("{{EXAMPLE}}", json.dumps(EXAMPLE, ensure_ascii=False, indent=2)),
        ("{{OPEN_ISSUES}}", _issues_text(mine)),
        ("{{ACCEPTED_RISKS}}", risks_text(accepted_risks or [])),
        ("{{REQUIREMENTS}}", req.strip()), ("{{HANDOFF}}", handoff.strip()),
    ]
    values = dict(pairs)
    # 一次性只替换模板自身的占位符：插入的内容（风险原文、交接包、要求）里即使出现 {{X}} 也原样保留
    return re.sub(r"\{\{[A-Z_]+\}\}", lambda m: values.get(m.group(0), m.group(0)), t)
