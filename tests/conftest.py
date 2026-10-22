import contextlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CREW = SKILL / "scripts" / "crew.py"
FAKE = Path(__file__).resolve().parent / "fake_reviewer.py"
sys.path.insert(0, str(SKILL / "scripts"))
os.environ["CREW_STUDIO_DIR"] = ""   # 测试不碰真实看板的 run.flag

HANDOFF = """任务：计划第 1 条「add 返回两数之和」
改了哪些文件：calc.py
为什么这么改：修正减号
怎么验证的：python3 -m pytest -q → 1 passed
自己担心的地方：无
请重点审：calc.py
"""

VERIFY = "python3 -m pytest -q -p no:cacheprovider"


def make_project(root, verify=None, timeouts=None):
    root = Path(root)
    root.mkdir(parents=True)
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    for t in ("t1", "t2"):
        (root / "crew" / t).mkdir(parents=True)
        (root / "crew" / t / "requirements.md").write_text("add(a, b) 必须返回 a + b。\n", encoding="utf-8")
        (root / "crew" / t / "handoff.md").write_text(HANDOFF, encoding="utf-8")
    cfg = {
        "scan": {"include": ["**/*.py"],
                 "exclude": ["crew/**", "**/__pycache__/**", ".pytest_cache/**"],
                 "symlinks": "reject"},
        "tasks": {
            "t1": {"review": "single", "requirements": "crew/t1/requirements.md",
                   "verify": [VERIFY] if verify is None else verify},
            "t2": {"review": "double", "requirements": "crew/t2/requirements.md", "verify": [VERIFY]},
        },
        "timeouts": timeouts or {"dsh_minutes": 1, "codex_minutes": 1, "verify_minutes": 2},
        "max_rounds": 2,
    }
    (root / "crew" / "crew.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def edit_cfg(project, fn):
    p = Path(project) / "crew" / "crew.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    fn(cfg)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def crew(project, *args, env_extra=None, timeout=180):
    fake = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    env = dict(os.environ, CREW_DSH_BIN=fake, CREW_CODEX_BIN=fake, DEEPSEEK_API_KEY="fake-key")
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(CREW), "--project", str(project), *args],
                       capture_output=True, text=True, env=env, timeout=timeout)
    return r.returncode, r.stdout + r.stderr


@contextlib.contextmanager
def hold_lock(td):
    """另起一个真实进程持有任务锁，直到退出 with 或被 kill。"""
    ready = Path(td).parent / f".{Path(td).name}.ready"
    code = (f"import sys, time\nsys.path.insert(0, {str(SKILL / 'scripts')!r})\n"
            "from pathlib import Path\nfrom crewlib.common import Lock\n"
            f"with Lock({str(td)!r}):\n    Path({str(ready)!r}).write_text('1')\n    time.sleep(120)\n")
    p = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(400):
            if ready.exists():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("持锁进程没有启动")
        yield p
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        ready.unlink(missing_ok=True)


@pytest.fixture
def proj(tmp_path):
    return make_project(tmp_path / "proj")
