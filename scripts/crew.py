#!/usr/bin/env python3
"""crew-review 调度脚本：snapshot / review / gate / approve。用法见 SKILL.md。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crewlib import baseline, config, decide, gate, reviewers, snapshot  # noqa: E402
from crewlib.common import CrewError, Lock, task_dir  # noqa: E402

GATE_EXIT = {"released": 0, "rejected": 1, "invalid": 1, "pending": 2, "needs_user": 3}


def _studio_flag():
    """Crew Studio 看板的“正在审查”标记；环境变量 CREW_STUDIO_DIR 为空字符串时关闭（测试用）。"""
    bundled = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "studio")
    default = bundled if os.path.isdir(bundled) else os.path.expanduser("~/crew-review-studio")
    d = os.environ.get("CREW_STUDIO_DIR", default)
    return os.path.join(d, "run.flag") if d and os.path.isdir(d) else None


def studio_running(on: bool, project=None) -> None:
    """开新一轮/开始审查时打开；gate 后若本项目已没有审查员在跑就关掉。看板只是展示，失败不影响流程。"""
    flag = _studio_flag()
    if not flag:
        return
    try:
        if on:
            open(flag, "a").close()
            return
        from crewlib.common import reviewer_busy
        crew_dir = os.path.join(os.path.abspath(project), "crew")
        for t in os.listdir(crew_dir) if os.path.isdir(crew_dir) else []:
            td = os.path.join(crew_dir, t)
            if os.path.isdir(td) and any(reviewer_busy(td, r) for r in ("dsh", "codex")):
                return
        if os.path.exists(flag):
            os.remove(flag)
    except OSError:
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="crew.py")
    ap.add_argument("--project", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot").add_argument("task")
    sub.add_parser("gate").add_argument("task")
    r = sub.add_parser("review")
    r.add_argument("task")
    r.add_argument("--by", choices=["dsh", "codex"], required=True)
    a = sub.add_parser("approve")
    a.add_argument("task")
    a.add_argument("--kind", choices=["change", "extra_round"], required=True)
    a.add_argument("--reason", required=True)
    d = sub.add_parser("decide")
    d.add_argument("task")
    d.add_argument("--accept", action="store_true", required=True)
    d.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd in ("snapshot", "review"):
            studio_running(True)
        if args.cmd == "snapshot":
            res = snapshot.snapshot(args.project, args.task)
            ok = sum(1 for v in res["verify"] if v["ok"])
            print(f"已开 round-{res['round']:02d}，指纹 {res['fingerprint'][:12]}…；"
                  f"必做验证通过 {ok}/{len(res['verify'])}")
            for v in res["verify"]:
                if not v["ok"]:
                    print(f"- 未通过：{v['cmd']}（退出码 {v['exit_code']}，超时 {v['timed_out']}）")
            return 0
        if args.cmd == "review":
            m = reviewers.run(args.project, args.task, args.by)
            if m["outcome"] == "external":
                print(f"{args.by} attempt-{m['attempt']:02d}：外部故障（{m['external_reason']}），"
                      "不计入审查次数；稍后重跑 review 即可")
                return 4
            print(f"{args.by} attempt-{m['attempt']:02d}：{m['outcome']}（{m['seconds']} 秒）")
            for e in m["errors"]:
                print(f"- {e}")
            return 0 if m["outcome"] in ("pass", "reject") else 2
        if args.cmd == "gate":
            rep = gate.gate(args.project, args.task)
            print(gate.describe(rep))
            studio_running(False, args.project)
            return GATE_EXIT[rep["status"]]
        if args.cmd == "decide":
            dec = decide.accept(args.project, args.task, args.reason)
            print(f"已记录用户裁决（round-{dec['round']:02d} 接受现状）：{dec['reason']}")
            for i in dec["accepted_issues"]:
                print(f"- 记入已接受风险：{args.task}:{i['id']}")
            return 0
        td = task_dir(args.project, args.task)
        rules = config.task_rules(config.load(args.project), args.task)
        req = config.read_requirements(args.project, rules)
        with Lock(td):
            e = baseline.approve(td, rules, req, args.kind, args.reason)
        print(f"已记录用户批准（{args.kind}）：{args.reason}")
        for item in e.get("items", []):
            print(f"- {item}")
        return 0
    except CrewError as e:
        print(f"拒绝：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
