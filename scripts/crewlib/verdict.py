"""审查结论：从模型输出取 JSON、按固定结构校验、生成中文报告（spec §8）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema" / "verdict.json"

TOP = ["task", "round", "fingerprint", "verdict", "blockers", "polish", "hardening", "prior_issues", "verification"]
BLOCKER = ["id", "summary", "where", "trigger", "actual", "why", "evidence", "speculative", "likelihood"]
POLISH = ["where", "note"]
HARDENING = ["where", "trigger", "note"]
# blocker 的触发概率：正常使用就会遇到 / 合理的单一故障（超时、崩溃、坏输入等）下会遇到。
# 需要特定崩溃时机、双重故障、进程号复用等极端条件才触发的，不是 blocker，放 hardening（只记账、不拦放行）。
LIKELIHOOD = ("normal_use", "plausible_failure")
LIKELIHOOD_LABEL = {"normal_use": "正常使用就会遇到", "plausible_failure": "合理故障下会遇到"}
PRIOR = ["id", "decision", "reason"]
BLOCKER_ONLY = [f for f in BLOCKER if f not in POLISH + HARDENING]   # id/summary/actual/why/evidence/speculative/likelihood
VERIF = ["command", "status", "note"]
VERDICTS = ("pass", "reject", "incomplete")
BID = re.compile(r"^B\d+$")
LABEL = {"pass": "通过", "reject": "退回", "incomplete": "未完成"}

EXAMPLE = {
    "task": "<照抄 CREW-META 的 task>", "round": 1, "fingerprint": "<照抄 CREW-META 的 fingerprint>",
    "verdict": "pass | reject | incomplete",
    "blockers": [{"id": "B1", "summary": "一句大白话说清是什么问题（30 字内，不写文件名和代码）", "where": "文件:行号", "trigger": "什么条件触发", "actual": "实际结果",
                  "why": "为什么违反要求", "evidence": "代码或输出原文", "speculative": False,
                  "likelihood": "normal_use | plausible_failure"}],
    "polish": [{"where": "文件:行号", "note": "可顺手改进的小问题"}],
    "hardening": [{"where": "文件:行号", "trigger": "需要的极端条件", "note": "加固建议"}],
    "prior_issues": [{"id": "dsh:R01:B1", "decision": "close | keep_open", "reason": "理由"}],
    "verification": [{"command": "你运行过的命令", "status": "passed | failed | not_run", "note": "说明"}],
}


def extract_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    for chunk in reversed(re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)):
        try:
            obj = json.loads(chunk.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    dec, found, i = json.JSONDecoder(), None, 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return found
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found = obj
        i = end  # 跳过整个对象，只取最外层，避免取到嵌套的小对象


def _items(v, key, fields, errors):
    items = v.get(key)
    if not isinstance(items, list):
        errors.append(f"{key} 必须是数组")
        return []
    good = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f"{key}[{i}] 必须是对象")
            continue
        miss = [f for f in fields if f not in it]
        if miss:
            errors.append(f"{key}[{i}] 缺少字段 {miss}")
            continue
        extra = [f for f in it if f not in fields]
        if extra:
            errors.append(f"{key}[{i}] 有多余字段 {extra}")
            continue
        bad = [f for f in fields if f != "speculative" and not isinstance(it[f], str)]
        if bad:
            errors.append(f"{key}[{i}] 这些字段必须是文本：{bad}")
            continue
        good.append(it)
    return good


def normalize(v) -> list:
    """校验前自动规整只影响格式、不影响结论的笔误，原地修改，返回中文说明（记入 meta.normalized）。
    · blocker 编号写成「dsh:R04:B1」「R2-B3」等：截取末尾的 B 编号。
    · polish / hardening（不拦放行）条目缺字段补「（未写）」、多余字段删除、非文本值转文本。
    blocker 缺字段、取值非法等仍交给 validate 判未完成，不在这里放水。polish / hardening 条目里若带
    blocker 特有字段（summary、actual、why 等），可能是真问题放错了位置，整条不动，交 validate 判未完成
    （审查 dsh:R01:B1：删掉这些字段会把 blocker 降级成无害备注放行）。"""
    notes = []
    if not isinstance(v, dict):
        return notes
    for b in v.get("blockers") or []:
        if isinstance(b, dict) and isinstance(b.get("id"), str) and not BID.match(b["id"]):
            m = re.search(r"B\d+\s*$", b["id"])
            if m:
                notes.append(f"blocker 编号「{b['id']}」规整为「{m.group(0).strip()}」")
                b["id"] = m.group(0).strip()
    for key, fields in (("polish", POLISH), ("hardening", HARDENING)):
        items = v.get(key)
        if not isinstance(items, list):
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict) or any(f in it for f in BLOCKER_ONLY):
                continue
            for f in fields:
                if f not in it:
                    it[f] = "（未写）"
                    notes.append(f"{key}[{i}] 缺字段 {f}，补「（未写）」")
                elif not isinstance(it[f], str):
                    it[f] = json.dumps(it[f], ensure_ascii=False) if not isinstance(it[f], (int, float)) else str(it[f])
                    notes.append(f"{key}[{i}].{f} 转为文本")
            for f in [f for f in it if f not in fields]:
                del it[f]
                notes.append(f"{key}[{i}] 删除多余字段 {f}")
    return notes


def format_only(errors) -> bool:
    """未完成是否只因结论格式不合规（可自动重审一次）；超时、退出码、写入违规、自评未完成等不算。"""
    hard = ("超时", "退出码", "写入/升权", "没有输出最终结论", "审查员自评未完成", "外部故障", "处理审查结论时出错",
            "无权关闭")
    return bool(errors) and not any(h in e for e in errors for h in hard)


def summary_of(rd) -> bool:
    """该轮冻结的结论格式是否要求 blocker 带一句话摘要（2026-10-14 起）。旧轮次不要求。"""
    p = Path(rd) / "frozen" / "verdict.schema.json"
    try:
        return "summary" in json.loads(p.read_text(encoding="utf-8"))["properties"]["blockers"]["items"]["required"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def severity_of(rd) -> bool:
    """该轮冻结的结论格式是否为 3.1 版（含问题分轻重）。没有冻结格式的旧轮次按 3.0 版校验。"""
    p = Path(rd) / "frozen" / "verdict.schema.json"
    try:
        return "hardening" in json.loads(p.read_text(encoding="utf-8"))["required"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validate(v, task: str, round_no: int, fp: str, open_ids: list, severity: bool = True, summary: bool = True) -> list:
    """severity=False 为 3.0 版格式（无 hardening、blocker 无 likelihood），用于校验旧轮次的历史记录。"""
    if not isinstance(v, dict):
        return ["结论不是 JSON 对象"]
    top = TOP if severity else [k for k in TOP if k != "hardening"]
    blocker_fields = BLOCKER if severity else [k for k in BLOCKER if k != "likelihood"]
    if not summary:
        blocker_fields = [k for k in blocker_fields if k != "summary"]
    errors = [f"缺少字段 {k}" for k in top if k not in v]
    errors += [f"有多余字段 {k}（schema 不允许）" for k in v if k not in top]
    if errors:
        return errors
    if v["task"] != task:
        errors.append(f"task 应为 {task}，实为 {v['task']!r}")
    if type(v["round"]) is not int or v["round"] != round_no:
        errors.append(f"round 应为 {round_no}，实为 {v['round']!r}")
    if v["fingerprint"] != fp:
        errors.append("结论里的指纹与本轮不符")
    if not isinstance(v["verdict"], str) or v["verdict"] not in VERDICTS:
        errors.append(f"verdict 只能是 {VERDICTS}")
    blockers = _items(v, "blockers", blocker_fields, errors)
    for b in blockers:
        if not BID.match(b["id"]):
            errors.append(f"blocker 编号必须形如 B1：{b['id']!r}")
        if not isinstance(b["speculative"], bool):
            errors.append(f"blocker {b['id']} 的 speculative 必须是 true/false")
        if severity and b["likelihood"] not in LIKELIHOOD:
            errors.append(f"blocker {b['id']} 的 likelihood 只能是 {LIKELIHOOD}；极端条件才触发的问题应放 hardening")
    ids = [b["id"] for b in blockers]
    if len(ids) != len(set(ids)):
        errors.append("blocker 编号重复")
    _items(v, "polish", POLISH, errors)
    if severity:
        _items(v, "hardening", HARDENING, errors)
    priors = _items(v, "prior_issues", PRIOR, errors)
    for p in priors:
        if p["decision"] not in ("close", "keep_open"):
            errors.append(f"prior_issues {p['id']} 的 decision 只能是 close 或 keep_open")
    seen = [p["id"] for p in priors]
    missing = [i for i in open_ids if i not in seen]
    extra = [i for i in seen if i not in open_ids]
    if missing:
        errors.append(f"prior_issues 漏表态：{missing}")
    if extra:
        errors.append(f"prior_issues 处理了不属于本审查员的未关闭问题（无权关闭）：{extra}")
    if len(seen) != len(set(seen)):
        errors.append("prior_issues 编号重复")
    for x in _items(v, "verification", VERIF, errors):
        if x["status"] not in ("passed", "failed", "not_run"):
            errors.append(f"verification 状态只能是 passed/failed/not_run：{x['status']!r}")
    if not errors:
        keep = [p for p in priors if p["decision"] == "keep_open"]
        if v["verdict"] == "pass" and (blockers or keep):
            errors.append("结论为 pass，却仍有 blocker 或保留未关闭的问题")
        if v["verdict"] == "reject" and not blockers and not keep:
            errors.append("结论为 reject，却没有任何 blocker 或保留的问题")
    return errors


def render_diagnostic(obj, raw: str, errors: list, reviewer: str, attempt: int) -> str:
    """结论不合格时的诊断报告：不假定任何字段的类型，只列原因和原文。"""
    lines = [f"# {reviewer} attempt-{attempt:02d}：结论未采用（格式错误或未完成）", "", "## 原因"]
    lines += [f"- {e}" for e in errors] or ["- 未知"]
    lines += ["", "## 审查员原始输出（截取前 4000 字）", "", "```", (raw or "")[:4000], "```"]
    return "\n".join(lines) + "\n"


def render_md(v: dict, reviewer: str, attempt: int) -> str:
    """只用于已通过 validate 的结论。"""
    lines = [f"# {reviewer} 审查结论（{v.get('task')} round-{v.get('round', 0):02d} attempt-{attempt:02d}）",
             "", f"结论：**{LABEL.get(v.get('verdict'), v.get('verdict'))}**", ""]
    lines.append("## 必须改（blocker）")
    for b in v.get("blockers") or []:
        tag = "（推测）" if b.get("speculative") else ""
        lines += [f"- **{b.get('id')}** {b.get('summary') or ''} `{b.get('where')}`{tag}（{LIKELIHOOD_LABEL.get(b.get('likelihood'), b.get('likelihood'))}）",
                  f"  - 触发：{b.get('trigger')}", f"  - 结果：{b.get('actual')}",
                  f"  - 为什么违反要求：{b.get('why')}", f"  - 依据：{b.get('evidence')}"]
    if not v.get("blockers"):
        lines.append("- 无")
    lines += ["", "## 历史问题表态"]
    for p in v.get("prior_issues") or []:
        word = "关闭" if p.get("decision") == "close" else "保留"
        lines.append(f"- {p.get('id')}：{word} —— {p.get('reason')}")
    if not v.get("prior_issues"):
        lines.append("- 无")
    lines += ["", "## 加固项（hardening：极端条件才触发，仅记录、不拦放行）"]
    lines += [f"- `{h.get('where')}` 条件：{h.get('trigger')} —— {h.get('note')}" for h in v.get("hardening") or []] or ["- 无"]
    lines += ["", "## 顺手改（polish，仅记录）"]
    lines += [f"- `{p.get('where')}` {p.get('note')}" for p in v.get("polish") or []] or ["- 无"]
    lines += ["", "## 审查员自述的核实（不作为放行依据）"]
    lines += [f"- `{x.get('command')}`：{x.get('status')} {x.get('note')}" for x in v.get("verification") or []] or ["- 无"]
    return "\n".join(lines) + "\n"
