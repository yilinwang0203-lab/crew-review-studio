"""每个命令取锁后先调用：把上次可能中断的状态转换补齐。"""
from __future__ import annotations

import os
import signal
import subprocess
import time

from . import evidence, issues, ledger
from .common import CRASH_PREFIX, CrewError, invalidate, read_json, reviewer_busy, round_dir, rounds, write_json


def _finish_adoption(rd, held=None) -> None:
    """处理上次中断的 attempt（每个命令取锁后都会先跑）。

    写入顺序为 final.txt → verdict.json/review.md → meta.json（attempt 完成标记）→ adopted.json（提交点）。
      - meta 记为 pass/reject 且 verdict.json 在：只差提交，补写 adopted.json；
      - meta 记为 incomplete：正常的未完成，允许同轮重跑；
      - 有 meta 记为 pass/reject 却缺 verdict.json，或无 meta 但任一原始输出已有结论：
        审查员已给出结论却没落定，交用户裁决；
      - 无 meta、也还没有任何结论（调度器在审查员运行中崩溃）：**整轮判失效**，必须重新 snapshot。
        不靠"把旧审查员杀干净"来保证正确性——孤儿进程即便事后交出结论，也只落在这个已作废轮次的
        旧目录里，不可能参与任何放行；结束孤儿进程只是尽力而为的清理。
    已作废轮次里登记过的孤儿 attempt（orphaned.json）事后出现的结论不再检查。
    """
    if (rd / "released.json").exists():
        return
    orphaned_before = set(read_json(rd / "orphaned.json")) if (rd / "orphaned.json").exists() else set()
    crashed = []
    for rev in ("dsh", "codex"):
        rv = rd / rev
        if not rv.is_dir():
            continue
        if rev != held and reviewer_busy(rd.parent, rev):
            continue   # 另一个调度进程正在运行该审查员（并行审查）：它会自己落定结论，不是崩溃
        adopted_k = read_json(rv / "adopted.json")["attempt"] if (rv / "adopted.json").exists() else None
        for ad in sorted(p for p in rv.glob("attempt-*") if p.is_dir()):
            if int(ad.name.split("-")[1]) == adopted_k:
                continue
            tag = f"{rev}/{ad.name}"
            mp = ad / "meta.json"
            meta = read_json(mp) if mp.exists() else None
            settled = meta is not None and meta.get("outcome") in ("pass", "reject") and not meta.get("errors")
            if settled and adopted_k is None and (ad / "verdict.json").exists():
                write_json(rv / "adopted.json", {"attempt": meta["attempt"], "verdict": meta["outcome"],
                                                 "at": meta.get("ended"), "recovered": True})
                adopted_k = meta["attempt"]
                continue
            if meta is not None and meta.get("outcome") in ("incomplete", "external"):
                continue
            if meta is None:
                if tag in orphaned_before:
                    continue
                _stop_orphan(ad)   # 尽力结束仍在运行的旧审查员（不作为正确性依据）
            if settled or evidence.raw_final(ad).strip():
                raise CrewError(f"{rd.name} 的 {rev} {ad.name} 上次在保存结论时中断，审查员已给出结论但未落定；"
                                "不能同轮重跑、开新一轮或放行，需要用户裁决")
            crashed.append(tag)
    # 先提交"整轮作废"，再登记孤儿：登记存在就意味着作废已落盘；
    # 万一旧版本或异常留下"有登记、无作废"，这里也补写作废（登记即作废的前提）
    tags = sorted(orphaned_before | set(crashed))
    if tags:
        invalidate(rd, "scheduler_crash", CRASH_PREFIX + "（" + "、".join(tags) + "），本轮作废，请重新 snapshot 开新一轮"
                   "（本轮不占轮次配额）")
    if crashed:
        write_json(rd / "orphaned.json", tags)


def _lstart(pid: int) -> str:
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return " ".join(r.stdout.split())


def _kill_group(pgid: int) -> None:
    # ESRCH：已不存在；EPERM：macOS 上已退出未回收的僵尸组，或别的用户的进程——都不再处理
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _stop_orphan(ad) -> None:
    """调度器被杀后，独立进程组里的审查员可能还在跑：确认身份后结束它，每个 attempt 只处理一次。

    - pid 文件（外壳写入"进程组号 启动时间"）完整：只有当前同号进程的启动时间一致才结束它，
      防止进程号被系统复用后误杀无关进程；
    - pid 文件缺失或为空（外壳已启动、还没执行 echo）：外壳命令行里带着本 attempt 的 pid 文件路径，
      按路径在进程表里找出它并结束；
    - 处理完写 orphan-handled，之后不再对这个 attempt 发任何信号。
    """
    done = ad / "orphan-handled"
    if done.exists():
        return
    pf = ad / "pid"
    parts = pf.read_text(encoding="utf-8", errors="replace").split() if pf.is_file() else []
    targets = []
    if len(parts) >= 2 and parts[0].isdigit():
        pgid = int(parts[0])
        if _lstart(pgid) == " ".join(parts[1:]):
            targets.append(pgid)
    elif not parts:
        r = subprocess.run(["ps", "-axo", "pgid=,command="], capture_output=True, text=True)
        key = str(pf)
        for line in r.stdout.splitlines():
            head, _, command = line.strip().partition(" ")
            # 只认本 attempt 的启动外壳：/bin/sh -c echo … <本 attempt 的 pid 路径> …（不误伤命令行里恰好含该路径的其他进程）
            if head.isdigit() and command.startswith("/bin/sh -c echo ") and f" {key} " in f"{command} ":
                targets.append(int(head))
    for pgid in sorted(set(targets)):
        _kill_group(pgid)
    done.write_text(f"targets={sorted(set(targets))}\n", encoding="utf-8")


def _record_violations(rd) -> None:
    """任一 dsh attempt 的事件流里有成功的写入/升权（哪怕调度器中途崩溃、没写 meta），本轮一律失效。"""
    if (rd / "INVALID").exists() or (rd / "released.json").exists():
        return
    for ad in sorted(p for p in (rd / "dsh").glob("attempt-*") if p.is_dir()):
        ev = ad / "events.jsonl"
        if ev.is_file():
            _, viol = evidence.dsh_scan(ev)
            if viol:
                invalidate(rd, "other", f"dsh {ad.name} 审查期间发生写入/升权：" + "；".join(viol))
                return


def recover(project, task: str, td, held=None) -> None:
    """held：调用方自己正持有的审查员锁（它名下未完成的 attempt 是以前崩溃留下的，照常判定）。"""
    for r in rounds(td):
        _record_violations(round_dir(td, r))
        _finish_adoption(round_dir(td, r), held)
    issues.save(td, issues.rebuild(td))
    for r in rounds(td):
        rd = round_dir(td, r)
        if (rd / "released.json").exists():
            ledger.ensure(project, task, rd, r)
