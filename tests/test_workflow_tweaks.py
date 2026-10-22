"""2026-10-14 改进：回复模板（6）、看板运行标记自动开关（7）、第 2 轮起只审改动（8）。"""
import json
import os
from pathlib import Path

from conftest import crew

from crewlib import prompts
from crewlib.common import task_dir


def _reject_round1(proj):
    assert crew(proj, "snapshot", "t1")[0] == 0
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    crew(proj, "gate", "t1")


def test_missing_reply_prints_template(proj):
    _reject_round1(proj)
    code, out = crew(proj, "snapshot", "t1")
    assert code != 0 and "回复模板" in out and "- dsh:R01:B1 已改（位置）/不改（理由）：calc.py:2" in out, out


def test_round2_prompt_scopes_to_changes(proj):
    _reject_round1(proj)
    (proj / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    hp = proj / "crew" / "t1" / "handoff.md"
    hp.write_text(hp.read_text(encoding="utf-8") + "\n- dsh:R01:B1 已改（calc.py:2）\n", encoding="utf-8")
    assert crew(proj, "snapshot", "t1")[0] == 0
    text = (task_dir(proj, "t1") / "round-02" / "frozen" / "prompt-dsh.md").read_text(encoding="utf-8")
    assert "第 2 轮复审" in text and "内容变化 calc.py" in text and "不再从中提出新的 blocker" in text
    r1 = (task_dir(proj, "t1") / "round-01" / "frozen" / "prompt-dsh.md").read_text(encoding="utf-8")
    assert "第 1 轮：按任务要求审查全部改动" in r1 and "{{SCOPE}}" not in r1


def test_scope_text_without_changes():
    assert "没有改动扫描范围内的文件" in prompts.scope_text(3, None)


def test_studio_flag_on_review_off_after_gate(proj, tmp_path):
    studio = tmp_path / "studio"
    studio.mkdir()
    env = {"CREW_STUDIO_DIR": str(studio)}
    assert crew(proj, "snapshot", "t1", env_extra=env)[0] == 0
    assert (studio / "run.flag").exists()
    (studio / "run.flag").unlink()
    crew(proj, "review", "t1", "--by", "dsh", env_extra=env)
    assert (studio / "run.flag").exists()
    crew(proj, "gate", "t1", env_extra=env)
    assert not (studio / "run.flag").exists()
