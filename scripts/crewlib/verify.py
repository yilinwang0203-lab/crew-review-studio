"""必做验证由脚本自己运行并留证据（spec §10）。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import fingerprint
from .common import kill_group, now, read_json, write_json


def run_all(project, rules: dict, vdir, manifest_files: dict) -> list:
    vdir = Path(vdir)
    vdir.mkdir()
    results = []
    for i, v in enumerate(rules["verify"], 1):
        d = vdir / f"{i:02d}"
        d.mkdir()
        minutes = v["timeout_minutes"] or rules["timeouts"]["verify_minutes"]
        started = now()
        with open(d / "output.log", "wb") as out:
            p = subprocess.Popen(["/bin/bash", "-c", v["cmd"]], cwd=str(project), stdout=out,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                code, timed_out = p.wait(timeout=minutes * 60), False
            except subprocess.TimeoutExpired:
                kill_group(p)
                code, timed_out = None, True
        text = (d / "output.log").read_text(encoding="utf-8", errors="replace")
        ok = (not timed_out) and code == 0 and (not v["expect"] or v["expect"] in text)
        res = {"cmd": v["cmd"], "cwd": str(project), "started": started, "ended": now(),
               "exit_code": code, "timed_out": timed_out, "expect": v["expect"], "ok": ok}
        write_json(d / "result.json", res)
        results.append(res)
    try:
        after = fingerprint.scan(project, rules["scan"]["include"], rules["scan"]["exclude"])
        write_json(vdir / "after.json", {"fingerprint": fingerprint.total(after),
                                         "diff": fingerprint.diff(manifest_files, after)})
    except fingerprint.SymlinkError as e:
        write_json(vdir / "after.json", {"fingerprint": None, "error": str(e)})
    return results


def problems(rd, rules: dict, round_fp: str) -> list:
    vdir = Path(rd) / "verify"
    out = []
    for i, v in enumerate(rules["verify"], 1):
        f = vdir / f"{i:02d}" / "result.json"
        if not f.exists():
            out.append(f"必做验证 {i} 缺少执行记录：{v['cmd']}")
            continue
        r = read_json(f)
        log = f.parent / "output.log"
        if r.get("cmd") != v["cmd"]:
            out.append(f"必做验证 {i} 的执行记录与冻结配置不符")
        elif not log.is_file():
            out.append(f"必做验证 {i} 缺少完整输出 output.log：{v['cmd']}")
        elif r.get("timed_out") or r.get("exit_code") != 0:   # 不信 ok 布尔，按原始判据重算
            out.append(f"必做验证 {i} 未通过（退出码 {r.get('exit_code')}，超时 {r.get('timed_out')}）：{v['cmd']}")
        elif v["expect"] and v["expect"] not in log.read_text(encoding="utf-8", errors="replace"):
            out.append(f"必做验证 {i} 的输出里没有要求的文本「{v['expect']}」：{v['cmd']}")
    if rules["verify"]:
        a = vdir / "after.json"
        if not a.exists():
            out.append("缺少运行验证后的指纹记录")
        elif read_json(a).get("fingerprint") != round_fp:
            out.append("运行必做验证后，扫描范围内文件发生了变化（验证命令写入了被审文件），本轮失效")
    return out
