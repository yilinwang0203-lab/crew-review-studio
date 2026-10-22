"""snapshot：开新一轮、冻结规则、锁定指纹、运行必做验证（spec §5、§6、§10）。"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import accepted_risks, baseline, config, fingerprint, gate, issues, prompts, recover, verify
from .common import CrewError, Lock, latest_round, read_json, reviewer_busy, round_dir, sha256_bytes, task_dir, write_json
from .verdict import SCHEMA_PATH

REQUIRED_HANDOFF = ["任务：", "改了哪些文件：", "为什么这么改：", "怎么验证的：", "自己担心的地方：", "请重点审："]


def handoff_problems(text: str, open_ids: list) -> list:
    lines = [l.strip() for l in text.splitlines()]
    out = [f"handoff.md 缺少段落「{h}」" for h in REQUIRED_HANDOFF if not any(l.startswith(h) for l in lines)]
    out += [f"handoff.md 没有回复未关闭问题 {i}" for i in open_ids if i not in text]
    return out


def reply_template(data: dict, handoff: str) -> list:
    """把还没回复的问题列成可直接粘进 handoff.md 的模板（编号必须用完整 ID，如 codex:R02:B1）。"""
    missing = [i for i in data.get("issues", []) if i.get("status") != "closed" and i["id"] not in handoff]
    if not missing:
        return []
    out = ["", "可直接粘进 handoff.md 的回复模板（把“已改（位置）/不改（理由）”改成实际内容）："]
    for i in missing:
        where = (i.get("text") or {}).get("where") or ""
        out.append(f"- {i['id']} 已改（位置）/不改（理由）：{where}")
    return out


def auto_gate(project, task: str, td, prev: int) -> None:
    """开新一轮前确保上一轮已按最新结论 gate 过：没 gate 过、或 gate 之后又有新结论/失效，就先在锁内跑一次。
    放行 → 交给后面的"已放行不能再开"拒绝；还有审查员没审完 → 拒绝；退回/需要裁决/失效 → 照常开轮。"""
    rd = round_dir(td, prev)
    if (rd / "released.json").exists():
        return
    gp = rd / "gate.json"
    rep = read_json(gp) if gp.exists() else None
    reviewers = read_json(rd / "frozen" / "rules.json")["reviewers"]
    if rep is None or rep.get("adopted_signature") != gate.adopted_signature(rd, reviewers):
        rep = gate._gate_locked(project, task, td)
        print("开新一轮前自动 gate 上一轮：\n" + gate.describe(rep))
    if rep["status"] == "pending":
        raise CrewError(f"round-{prev:02d} 还有审查员没审完（{'、'.join(rep['pending'])}），先跑完 review 再开新一轮")


def snapshot(project, task: str) -> dict:
    project = Path(project).resolve()
    td = task_dir(project, task)
    rules = config.task_rules(config.load(project), task)
    req = config.read_requirements(project, rules)
    with Lock(td):
        recover.recover(project, task, td)
        for rev in ("dsh", "codex"):
            if reviewer_busy(td, rev):
                raise CrewError(f"{rev} 还在审查本任务的当前轮次，等它结束后再开新一轮")
        prev = latest_round(td)
        if prev is not None:
            auto_gate(project, task, td, prev)
        if prev is not None and (round_dir(td, prev) / "released.json").exists():
            raise CrewError(f"{task} 已在 round-{prev:02d} 放行，不能再开新一轮；放行后的改动请作为新任务送审")
        hp = td / "handoff.md"
        if not hp.exists():
            raise CrewError(f"缺少交接包 {hp}")
        handoff = hp.read_text(encoding="utf-8")
        data = issues.load(td)   # recover 刚重放过
        if prev is not None:
            issues.revert_closing(data, f"round-{prev:02d} 未放行")
        probs = handoff_problems(handoff, issues.open_ids(data))
        if probs:
            raise CrewError("\n".join(probs + reply_template(data, handoff)))
        risks = accepted_risks.sync(project)["risks"]   # 补齐已提交裁决，取一致快照；坏清单在这里就拒绝开轮
        files = fingerprint.scan(project, rules["scan"]["include"], rules["scan"]["exclude"])
        n = (prev or 0) + 1
        base = baseline.check_and_update(td, rules, req, n)
        baseline.save(td, base)   # 先落基线再建轮次：中途崩溃时不会出现"有轮次、无基线"
        rd = round_dir(td, n)
        rd.mkdir()
        fp = fingerprint.total(files)
        issues.record_replies(data, handoff, n)
        fz = rd / "frozen"
        fz.mkdir()
        shutil.copyfile(project / "crew" / "crew.json", fz / "crew.json")
        (fz / "handoff.md").write_text(handoff, encoding="utf-8")
        (fz / "requirements.md").write_text(req, encoding="utf-8")
        write_json(fz / "rules.json", rules)
        shutil.copyfile(SCHEMA_PATH, fz / "verdict.schema.json")   # 结论格式随轮次冻结
        write_json(fz / "accepted-risks.json", {"risks": risks})   # 已接受风险随轮次冻结，之后改清单不影响本轮
        changes = None
        if prev is not None and (round_dir(td, prev) / "manifest.txt").exists():
            old = fingerprint.parse_manifest((round_dir(td, prev) / "manifest.txt").read_text(encoding="utf-8"))
            changes = fingerprint.describe(fingerprint.diff(old, files)) or None
        scope = prompts.scope_text(n, changes)
        for rev in rules["reviewers"]:
            mine = issues.open_for(data, rev)
            write_json(fz / f"open-issues-{rev}.json", mine)
            (fz / f"prompt-{rev}.md").write_text(prompts.build(rev, task, n, fp, handoff, req, mine, scope, risks,
                                                               rules["verify"]),
                                                 encoding="utf-8")
        write_json(fz / "hashes.json", {p.name: sha256_bytes(p.read_bytes()) for p in sorted(fz.iterdir())})
        (rd / "manifest.txt").write_text(fingerprint.manifest_text(files), encoding="utf-8")
        (rd / "fingerprint.txt").write_text(fp + "\n", encoding="utf-8")
        issues.save(td, data)
        results = verify.run_all(project, rules, rd / "verify", files)
        return {"round": n, "fingerprint": fp, "verify": results}
