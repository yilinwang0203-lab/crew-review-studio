"""外部故障识别（容量不足、断网、限流、额度用完）与退避时长；看板只做展示，计费口径以这里为准。"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .common import CrewError

# (关键词, 类别, 原因, 是否值得等待重试)；额度类排在前面：同时出现断网字样时以额度为准，不白等
HINTS = (
    ("usage limit", "quota", "账户额度用完", False),
    ("insufficient_quota", "quota", "账户额度用完", False),
    ("exceeded your current quota", "quota", "账户额度用完", False),
    ("insufficient balance", "quota", "账户余额不足", False),
    ("quota", "quota", "账户额度用完", False),
    ("401 unauthorized", "auth", "登录失效", False),
    ("at capacity", "capacity", "模型容量不足", True),
    ("overloaded", "capacity", "模型容量不足", True),
    ("service unavailable", "capacity", "服务暂时不可用（503）", True),
    ("too busy", "capacity", "服务繁忙", True),
    ("too many requests", "rate_limit", "请求被限流（429）", True),
    ("rate limit", "rate_limit", "请求被限流", True),
    ("stream disconnected", "network", "网络连接中断", True),
    ("connection reset", "network", "网络连接中断", True),
    ("econnreset", "network", "网络连接中断", True),
    ("network error", "network", "网络错误", True),
    ("error sending request", "network", "网络错误", True),
    ("connection error", "network", "网络连接错误", True),
    ("connection refused", "network", "网络连接被拒绝", True),
    ("econnrefused", "network", "网络连接被拒绝", True),
    ("could not resolve host", "network", "域名解析失败", True),
    ("enotfound", "network", "域名解析失败", True),
)
BACKOFF = (60, 120, 240)
TAIL = 2_000_000


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - TAIL))
            return f.read().decode("utf-8", "ignore")
    except OSError:
        return ""


def _error_events(text: str) -> str:
    """只取事件流里的错误类事件：审查员读到的代码/工具输出里出现关键词不算外部故障。"""
    out = []
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = str(e.get("type", "")) if isinstance(e, dict) else ""
        if "error" in t or "fail" in t:
            out.append(json.dumps(e, ensure_ascii=False))
    return "\n".join(out)


def classify(ad) -> dict | None:
    """在 attempt 目录的 stderr 与错误事件里找外部故障；返回 {kind, reason, retryable}，未命中返回 None。"""
    ad = Path(ad)
    low = (_tail(ad / "stderr.log") + "\n" + _error_events(_tail(ad / "events.jsonl"))).lower()
    for key, kind, reason, retryable in HINTS:
        if key in low:
            return {"kind": kind, "reason": reason, "retryable": retryable}
    return None


def backoff_seconds(env=None) -> list:
    """三次重试前各等多久；CREW_EXTERNAL_BACKOFF_SCALE 为倍率（测试用 0）。"""
    raw = (os.environ if env is None else env).get("CREW_EXTERNAL_BACKOFF_SCALE", "1")
    try:
        scale = float(raw)
    except ValueError:
        scale = -1
    if not math.isfinite(scale) or scale < 0:
        raise CrewError(f"CREW_EXTERNAL_BACKOFF_SCALE 必须是非负数：{raw}")
    return [s * scale for s in BACKOFF]
