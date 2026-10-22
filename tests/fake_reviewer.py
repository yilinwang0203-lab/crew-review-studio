#!/usr/bin/env python3
"""测试用假审查员：同时扮演 dsh（--profile headless --json -）和 codex（exec … -o FILE -）。

FAKE_MODE（可被 FAKE_MODE_DSH / FAKE_MODE_CODEX 覆盖）：
  pass | reject | incomplete | empty | garbage | sleep | exit1 | write | omit_prior | close_foreign
  | prefixed_id | hardening_no_trigger | blocker_no_trigger（格式笔误，测自动规整）
FAKE_FOREIGN_ID：close_foreign 模式下要越权关闭的问题 ID
FAKE_PIDFILE：sleep 模式下记录子进程 pid
FAKE_DELAY：给结论前先睡几秒（并行测试用）
FAKE_SEQ + FAKE_SEQ_FILE：按调用次数依次取模式（逗号分隔，用完后重复最后一个；--version 不消耗）
外部故障模式：ext_capacity（错误事件，退出 1）| ext_net（stderr 断网，退出 1）| ext_quota（错误事件额度用完，退出 1）
  | tool_mentions（工具输出里提到 rate limit，但审查正常通过）
"""
import json
import os
import re
import subprocess
import sys
import time


def main():
    argv = sys.argv[1:]
    if argv[-1:] == ["--version"]:
        print("fake-reviewer 0.0.1")
        return
    prompt = sys.stdin.read()
    is_codex = "exec" in argv
    mode = os.environ.get("FAKE_MODE_CODEX" if is_codex else "FAKE_MODE_DSH") or os.environ.get("FAKE_MODE", "pass")
    if os.environ.get("FAKE_SEQ"):
        seq, cf = os.environ["FAKE_SEQ"].split(","), os.environ["FAKE_SEQ_FILE"]
        n = int(open(cf).read()) if os.path.exists(cf) else 0
        with open(cf, "w") as f:
            f.write(str(n + 1))
        mode = seq[min(n, len(seq) - 1)]
    ext = {"ext_capacity": "Selected model is at capacity. Please try a different model.",
           "ext_quota": "You've hit your usage limit. insufficient_quota"}
    if mode in ext:
        if os.environ.get("FAKE_EXT_WRITE"):   # 故障前已成功写过文件（违规）
            print(json.dumps({"type": "tool_call", "callId": "w1", "tool": "write", "input": {"file_path": "x.txt"}}))
            print(json.dumps({"type": "tool_result", "callId": "w1", "status": "completed", "result": "ok"}))
        err = {"type": "error", "message": ext[mode]}
        print(json.dumps(err, ensure_ascii=False))
        print(json.dumps({"type": "turn.failed", "error": err}, ensure_ascii=False))
        sys.exit(1)
    if mode == "ext_net":
        print("ERROR: stream disconnected before completion: error sending request", file=sys.stderr)
        sys.exit(1)
    meta = re.search(r"CREW-META task=(\S+) round=(\d+) fingerprint=(\S+)", prompt)
    line = re.search(r"CREW-OPEN-ISSUES: (.*)", prompt)
    open_ids = [i.strip() for i in (line.group(1).split(",") if line else []) if i.strip() and i.strip() != "无"]
    if os.environ.get("FAKE_DELAY"):
        time.sleep(float(os.environ["FAKE_DELAY"]))   # 模拟审查耗时（并行测试用）
    if mode == "sleep":
        child = subprocess.Popen(["sleep", "300"])
        if os.environ.get("FAKE_PIDFILE"):
            with open(os.environ["FAKE_PIDFILE"], "w") as f:
                f.write(str(child.pid))
        time.sleep(300)
    if mode == "exit1":
        sys.exit(1)
    events = [{"type": "session", "sessionId": "fake"}]
    if mode == "tool_mentions":
        events += [{"type": "tool_result", "callId": "c0", "status": "completed",
                    "result": "EXTERNAL = ('rate limit', 'at capacity', 'usage limit')"}]
        print("note: rate limit mentioned in code under review", file=sys.stderr)
        mode = "garbage" if os.environ.get("FAKE_MENTION_GARBAGE") else "pass"
    if mode == "write":
        events += [{"type": "tool_call", "callId": "c1", "tool": "write", "input": {"file_path": "x.txt"}},
                   {"type": "tool_result", "callId": "c1", "status": "completed", "result": "ok"}]
    if mode == "empty":
        final = ""
    elif mode == "garbage":
        final = "{这不是 JSON"
    else:
        v = {"task": meta.group(1), "round": int(meta.group(2)), "fingerprint": meta.group(3),
             "verdict": "pass", "blockers": [], "polish": [{"where": "calc.py:1", "note": "可加文档字符串"}],
             "hardening": [{"where": "calc.py:1", "trigger": "极端条件",
                            "note": "加固建议示例" + os.environ.get("FAKE_TAG", "")}],
             "prior_issues": [{"id": i, "decision": "close", "reason": "已核实修复"} for i in open_ids],
             "verification": [{"command": "python3 -m pytest -q", "status": "passed", "note": "自述"}]}
        if mode == "reject":
            v["verdict"] = "reject"
            v["blockers"] = [{"id": "B1", "summary": "加法算成了减法", "where": "calc.py:2", "trigger": "调用 add(1, 2)", "actual": "返回 -1",
                              "why": "要求返回两数之和", "evidence": "return a - b", "speculative": False,
                              "likelihood": "normal_use"}]
        elif mode == "incomplete":
            v["verdict"] = "incomplete"
        elif mode == "omit_prior":
            v["prior_issues"] = []
        elif mode == "close_foreign":
            v["prior_issues"].append({"id": os.environ["FAKE_FOREIGN_ID"], "decision": "close", "reason": "越权"})
        elif mode == "round_str":
            v["round"] = str(v["round"])
        elif mode == "round_float":
            v["round"] = float(v["round"])
        elif mode == "blockers_str":
            v["blockers"] = "x"
        elif mode == "blockers_item":
            v["verdict"], v["blockers"] = "reject", [1]
        elif mode == "extra_field":
            v["unknown"] = 1
        elif mode == "extra_nested":
            v["polish"][0]["extra"] = "x"
        elif mode == "prefixed_id":            # 实测：dsh 把 blocker 编号写成历史问题格式
            v["verdict"] = "reject"
            v["blockers"] = [{"id": "dsh:R04:B1", "summary": "加法算成了减法", "where": "calc.py:2", "trigger": "调用 add(1, 2)",
                              "actual": "返回 -1", "why": "要求返回两数之和", "evidence": "return a - b",
                              "speculative": False, "likelihood": "normal_use"}]
        elif mode == "hardening_no_trigger":   # 实测：加固项漏 trigger
            del v["hardening"][0]["trigger"]
        elif mode == "blocker_no_trigger":     # blocker 缺字段：不规整，仍判未完成
            v["verdict"] = "reject"
            v["blockers"] = [{"id": "B1", "summary": "x", "where": "calc.py:2", "actual": "-1", "why": "w",
                              "evidence": "e", "speculative": False, "likelihood": "normal_use"}]
        final = json.dumps(v, ensure_ascii=False)
    if is_codex:
        with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as f:
            f.write(final)
        print(json.dumps({"type": "thread.started"}))
    else:
        events.append({"type": "final", "text": final})
        for e in events:
            print(json.dumps(e, ensure_ascii=False))


main()
