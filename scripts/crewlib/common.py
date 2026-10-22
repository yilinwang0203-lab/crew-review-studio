"""公共工具：错误类型、时间、哈希、JSON、路径、锁、进程组终止。"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path

MAX_ATTEMPTS = 3
TASK_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class CrewError(Exception):
    """给用户看的拒绝或错误；命令行打印后以退出码 1 结束。"""


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        if default is not None:
            return default
        raise CrewError(f"缺少文件：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def write_text_atomic(path, text: str) -> None:
    """原子写入：先写临时文件再 os.replace，崩溃时只会看到旧版或新版，不会看到半个文件。"""
    p = Path(path)
    tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def write_json(path, obj) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def crash_point(name: str) -> None:
    """测试用故障注入：环境变量 CREW_CRASH_AT 等于 name 时，立即以 SIGKILL 结束本进程。"""
    if os.environ.get("CREW_CRASH_AT") == name:
        os.kill(os.getpid(), signal.SIGKILL)


def task_dir(project, task: str) -> Path:
    if not TASK_RE.match(task):
        raise CrewError(f"任务名只能用字母、数字、- 和 _：{task}")
    return Path(project) / "crew" / task


def round_dir(td, n: int) -> Path:
    return Path(td) / f"round-{n:02d}"


def rounds(td) -> list:
    out = []
    for p in Path(td).glob("round-*"):
        m = re.fullmatch(r"round-(\d{2,})", p.name)
        if m and p.is_dir():
            out.append(int(m.group(1)))
    return sorted(out)


def _attempt_dirs(rv) -> list:
    return [p for p in Path(rv).glob("attempt-*") if p.is_dir() and re.fullmatch(r"attempt-\d{2,}", p.name)]


def counted_attempts(rv) -> int:
    """计入每轮 MAX_ATTEMPTS 的 attempt 数：已落定为外部故障（external）的不算。"""
    n = 0
    for ad in _attempt_dirs(rv):
        mp = ad / "meta.json"
        if not (mp.exists() and read_json(mp).get("outcome") == "external"):
            n += 1
    return n


def next_attempt(rv) -> int:
    """新 attempt 的编号：已有最大编号加一（外部故障不计数，但目录不复用）。"""
    return max((int(p.name.split("-")[1]) for p in _attempt_dirs(rv)), default=0) + 1


CRASH_PREFIX = "调度器在审查员运行中途崩溃"


def invalidate(rd, kind: str, reason: str) -> None:
    """判本轮失效。INVALID（文本）是提交点，已有则保留最初原因；INVALID.json 记结构化类别，供轮次计费判断。
    kind：scheduler_crash（调度器中途崩溃，不占轮次配额）或 other。"""
    rd = Path(rd)
    if (rd / "INVALID").exists():
        return
    write_text_atomic(rd / "INVALID", reason.rstrip("\n") + "\n")
    crash_point("after_invalid_text")
    write_json(rd / "INVALID.json", {"kind": kind, "reason": reason.strip(), "at": now()})


def crash_invalidated(rd) -> bool:
    """本轮是否因调度器中途崩溃而作废（不占轮次配额）。优先看 INVALID.json；没有时（旧数据或刚好崩在
    两次写入之间）回退到 INVALID 文本是否以崩溃措辞开头。"""
    rd = Path(rd)
    if not (rd / "INVALID").exists():
        return False
    jp = rd / "INVALID.json"
    if jp.exists():
        try:
            kind = json.loads(jp.read_text(encoding="utf-8"))["kind"]
        except (ValueError, KeyError, TypeError) as e:
            kind = f"无法解析（{type(e).__name__}）"
        if kind not in ("scheduler_crash", "other"):
            raise CrewError(f"{jp} 已损坏（kind={kind!r}），无法判断本轮是否计入轮次，需人工处理")
        return kind == "scheduler_crash"
    return (rd / "INVALID").read_text(encoding="utf-8").startswith(CRASH_PREFIX)


def latest_round(td):
    r = rounds(td)
    return r[-1] if r else None


class Lock:
    """每个任务一把锁，用内核 flock 互斥。

    锁文件只是挂锁的载体，永不删除；持锁进程无论正常退出还是崩溃，内核都会释放锁，
    因此不存在"旧锁"需要清理，也不会误删别人的锁。文件里的 pid 只用于提示。
    """

    def __init__(self, td, wait: bool = False, name: str = ".lock", busy_msg: str = None):
        self.path = Path(td) / name
        self.wait = wait
        self.busy_msg = busy_msg
        self.f = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX if self.wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            holder = f.read().strip() or "未知"
            f.close()
            raise CrewError(f"{self.busy_msg or '该任务正在被另一个调度进程使用'}（pid={holder}，锁文件 {self.path}）")
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
        self.f = f
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()
        self.f = None


def reviewer_lock(td, reviewer: str) -> "Lock":
    """每个审查员一把锁：审查员运行全程持有，dsh 与 Codex 互不阻塞，可并行。"""
    return Lock(td, name=f".lock-{reviewer}", busy_msg=f"{reviewer} 已经在审查这个任务")


def reviewer_busy(td, reviewer: str) -> bool:
    """该审查员是否正被另一个调度进程运行（只探测，不持有锁）。"""
    path = Path(td) / f".lock-{reviewer}"
    if not path.exists():
        return False
    with open(path, "a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return False


def kill_group(proc) -> None:
    """终止 proc 所在的整个进程组（proc 需以 start_new_session=True 启动）。

    ESRCH：组已不存在；EPERM：macOS 上组长已回收、只剩僵尸的进程组——都视为已结束。
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()
