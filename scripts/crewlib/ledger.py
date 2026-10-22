"""账本 crew/ledger.md：只按 released.json 写；每条记录有首尾标记，整文件原子替换，残缺记录会被修复。"""
from __future__ import annotations

from pathlib import Path

from .common import Lock, read_json, write_text_atomic


def _start(task: str, n: int) -> str:
    return f"<!-- crew-release {task} round-{n:02d} -->"


def _end(task: str, n: int) -> str:
    return f"<!-- /crew-release {task} round-{n:02d} -->"


def _suggestions(td, upto: int):
    """汇总本任务第 1..upto 轮所有被采用结论里的 polish 与加固项（退回轮次的建议也不能丢），去重保序。"""
    polish, hardening = [], []
    for k in range(1, upto + 1):
        rdk = Path(td) / f"round-{k:02d}"
        for rev in ("dsh", "codex"):
            ap = rdk / rev / "adopted.json"
            if not ap.exists():
                continue
            a = read_json(ap)
            v = read_json(rdk / rev / f"attempt-{a['attempt']:02d}" / "verdict.json")
            for p in v.get("polish", []):
                line = f"  - [{rev} R{k:02d}] {p['where']} —— {p['note']}"
                if line not in polish:
                    polish.append(line)
            for h in v.get("hardening", []):
                line = f"  - [{rev} R{k:02d}] {h['where']} 条件：{h['trigger']} —— {h['note']}"
                if line not in hardening:
                    hardening.append(line)
    return polish, hardening


def ensure(project, task: str, rd, n: int) -> None:
    """账本是多个任务共享的文件：读-改-写整段放在项目级锁 crew/.lock 内（等待而非报错），防止互相覆盖。"""
    with Lock(Path(project) / "crew", wait=True):
        _ensure(project, task, rd, n)


def _ensure(project, task: str, rd, n: int) -> None:
    path = Path(project) / "crew" / "ledger.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    start, end = _start(task, n), _end(task, n)
    if end in text:
        return
    if start in text:   # 只写了一半的旧记录：从开始标记截掉，重新完整写
        text = text[:text.index(start)].rstrip("\n") + "\n"
    rel = read_json(Path(rd) / "released.json")
    lines = [start, f"## {task} round-{n:02d} 放行（{rel['at']}）", f"- 指纹：{rel['fingerprint']}"]
    for rev, r in rel["reviews"].items():
        lines.append(f"- {rev}：attempt-{r['attempt']:02d} 通过")
    polish, hardening = _suggestions(Path(rd).parent, n)
    lines.append("- polish（仅记录，不承诺处理）：" + ("" if polish else "无"))
    lines += polish
    lines.append("- 加固项（极端条件，仅记录、不拦放行）：" + ("" if hardening else "无"))
    lines += hardening
    lines.append(end)
    if not text.strip():
        text = "# crew 审查账本\n"
    write_text_atomic(path, text + "\n" + "\n".join(lines) + "\n")
