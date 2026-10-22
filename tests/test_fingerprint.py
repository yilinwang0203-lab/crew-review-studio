import os
import subprocess
import sys
import time

import pytest

from conftest import SKILL, hold_lock
from crewlib import fingerprint as fpm
from crewlib.common import CrewError, Lock, latest_round, round_dir, rounds, task_dir


def write(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_glob_rules():
    r = fpm.glob_to_regex
    assert r("**/*.py").match("a.py") and r("**/*.py").match("x/y/a.py")
    assert not r("*.py").match("x/a.py")
    assert r("templates/**").match("templates/a/b.html")
    assert r("**/__pycache__/**").match("a/__pycache__/m.pyc")


def test_scan_include_exclude_and_chinese_paths(tmp_path):
    write(tmp_path / "a.py")
    write(tmp_path / "部署包 副本" / "模块.py", "y")
    write(tmp_path / "notes.txt")
    write(tmp_path / "crew" / "t1" / "x.py")
    files = fpm.scan(tmp_path, ["**/*.py"], ["crew/**"])
    assert sorted(files) == ["a.py", "部署包 副本/模块.py"]


def test_excluded_dir_not_entered_even_with_symlinks(tmp_path):
    write(tmp_path / "a.py")
    (tmp_path / "runs").mkdir()
    os.symlink(tmp_path / "a.py", tmp_path / "runs" / "link.py")
    os.symlink(tmp_path, tmp_path / "runs" / "loop")
    assert list(fpm.scan(tmp_path, ["**/*.py"], ["runs/**"])) == ["a.py"]


@pytest.mark.parametrize("kind", ["file", "dir", "broken"])
def test_symlinks_in_scope_rejected(tmp_path, kind):
    write(tmp_path / "real" / "b.py")
    if kind == "file":
        os.symlink(tmp_path / "real" / "b.py", tmp_path / "entry.py")
    elif kind == "dir":
        os.symlink(tmp_path / "real", tmp_path / "linked")
    else:
        os.symlink(tmp_path / "missing.py", tmp_path / "broken.py")
    with pytest.raises(fpm.SymlinkError) as e:
        fpm.scan(tmp_path, ["**/*.py"], ["crew/**"])
    assert isinstance(e.value, CrewError) and e.value.paths


def test_manifest_roundtrip_and_diff():
    old = {"a.py": "1", "b.py": "2", "c.py": "3"}
    new = {"a.py": "1", "b2.py": "2", "c.py": "9", "d.py": "4"}
    assert fpm.parse_manifest(fpm.manifest_text(old)) == old
    assert fpm.total(old) != fpm.total(new)
    d = fpm.diff(old, new)
    assert d == {"added": ["d.py"], "removed": [], "renamed": [["b.py", "b2.py"]], "changed": ["c.py"]}
    assert not fpm.is_clean(d) and fpm.is_clean(fpm.diff(old, dict(old)))
    assert "改名" in fpm.describe(d)


def test_round_paths(tmp_path):
    td = task_dir(tmp_path, "t1")
    assert latest_round(td) is None
    round_dir(td, 1).mkdir(parents=True)
    round_dir(td, 2).mkdir()
    assert rounds(td) == [1, 2] and latest_round(td) == 2
    with pytest.raises(CrewError):
        task_dir(tmp_path, "../x")


def test_lock_held_by_other_process_refused_then_released_on_kill(tmp_path):
    td = tmp_path / "crew" / "t1"
    td.mkdir(parents=True)
    with hold_lock(td) as holder:
        with pytest.raises(CrewError, match="另一个调度进程"):
            with Lock(td):
                pass
        holder.kill()          # 模拟调度进程崩溃（SIGKILL，不走 __exit__）
        holder.wait()
    with Lock(td):             # 内核已释放，直接拿到
        assert (td / ".lock").read_text() == str(os.getpid())
    assert (td / ".lock").exists()   # 锁文件不删除


def test_lock_race_only_one_winner(tmp_path):
    """8 个真实进程同时抢锁，任何时刻只能有一个在临界区内。"""
    td = tmp_path / "crew" / "t1"
    td.mkdir(parents=True)
    go = tmp_path / "go"
    code = (
        "import os, sys, time\n"
        f"sys.path.insert(0, {str(SKILL / 'scripts')!r})\n"
        "from pathlib import Path\n"
        "from crewlib.common import Lock, CrewError\n"
        f"go, td, log = Path({str(go)!r}), Path({str(td)!r}), Path({str(tmp_path / 'log')!r})\n"
        "while not go.exists(): time.sleep(0.001)\n"
        "try:\n"
        "    with Lock(td):\n"
        "        with open(log, 'a') as f: f.write(f'in {time.monotonic()}\\n')\n"
        "        time.sleep(0.3)\n"
        "        with open(log, 'a') as f: f.write(f'out {time.monotonic()}\\n')\n"
        "except CrewError:\n"
        "    pass\n")
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(8)]
    time.sleep(0.5)
    go.write_text("1")
    for p in procs:
        assert p.wait(timeout=30) == 0
    events = [l.split() for l in (tmp_path / "log").read_text().splitlines()]
    depth = 0
    for kind, t in sorted(events, key=lambda e: float(e[1])):
        depth += 1 if kind == "in" else -1
        assert depth <= 1
    assert sum(1 for e in events if e[0] == "in") >= 1
