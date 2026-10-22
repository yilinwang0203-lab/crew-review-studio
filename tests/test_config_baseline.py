import pytest

from conftest import edit_cfg
from crewlib import baseline, config
from crewlib.common import CrewError


def rules_of(proj, task="t1"):
    r = config.task_rules(config.load(proj), task)
    return r, config.read_requirements(proj, r)


def test_load_and_task_rules(proj):
    r, req = rules_of(proj)
    assert r["reviewers"] == ["dsh"] and r["verify"][0]["expect"] is None
    assert r["timeouts"]["verify_minutes"] == 2 and r["max_rounds"] == 2
    assert config.task_rules(config.load(proj), "t2")["reviewers"] == ["dsh", "codex"]
    assert "a + b" in req


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c["scan"]["exclude"].remove("crew/**"), "crew/\\*\\*"),
    (lambda c: c["scan"].__setitem__("symlinks", "follow"), "symlinks"),
    (lambda c: c["tasks"]["t1"].__setitem__("review", "triple"), "single 或 double"),
    (lambda c: c["tasks"]["t1"].pop("requirements"), "requirements"),
])
def test_bad_config_refused(proj, mutate, msg):
    edit_cfg(proj, mutate)
    with pytest.raises(CrewError, match=msg):
        config.task_rules(config.load(proj), "t1")


def test_first_round_creates_baseline(proj):
    td = proj / "crew" / "t1"
    r, req = rules_of(proj)
    base = baseline.check_and_update(td, r, req, 1)
    baseline.save(td, base)
    assert baseline.load(td)["review"] == "single" and baseline.round_limit(base) == 2


@pytest.mark.parametrize("mutate,word", [
    (lambda c: c["tasks"]["t2"].__setitem__("review", "single"), "审查级别"),
    (lambda c: c["tasks"]["t2"].__setitem__("verify", []), "必做验证"),
    (lambda c: c["scan"]["include"].append("**/*.html") or c["scan"]["include"].remove("**/*.py"), "include"),
    (lambda c: c["scan"]["exclude"].append("calc.py"), "exclude"),
    (lambda c: c.__setitem__("max_rounds", 5), "max_rounds"),
])
def test_loosening_needs_approval(proj, mutate, word):
    td = proj / "crew" / "t2"
    r, req = rules_of(proj, "t2")
    baseline.save(td, baseline.check_and_update(td, r, req, 1))
    edit_cfg(proj, mutate)
    r2, req2 = rules_of(proj, "t2")
    with pytest.raises(CrewError, match=word):
        baseline.check_and_update(td, r2, req2, 2)
    baseline.approve(td, r2, req2, "change", "用户同意")
    base = baseline.check_and_update(td, r2, req2, 2)
    appr = base["approvals"][-1]
    assert appr["applied"] and appr["before"]["review"] == "double"


def test_requirements_change_needs_approval_and_keeps_history(proj):
    td = proj / "crew" / "t1"
    r, req = rules_of(proj)
    baseline.save(td, baseline.check_and_update(td, r, req, 1))
    (proj / "crew" / "t1" / "requirements.md").write_text("add 可以返回任何值。\n", encoding="utf-8")
    r2, req2 = rules_of(proj)
    with pytest.raises(CrewError, match="任务要求原文"):
        baseline.check_and_update(td, r2, req2, 2)
    baseline.approve(td, r2, req2, "change", "需求变了")
    base = baseline.check_and_update(td, r2, req2, 2)
    assert base["approvals"][-1]["before"]["requirements_text"].startswith("add(a, b)")
    assert base["requirements_text"].startswith("add 可以")


def test_stale_approval_does_not_cover_other_change(proj):
    td = proj / "crew" / "t2"
    r, req = rules_of(proj, "t2")
    baseline.save(td, baseline.check_and_update(td, r, req, 1))
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("review", "single"))
    r2, req2 = rules_of(proj, "t2")
    baseline.approve(td, r2, req2, "change", "只批了降级")
    edit_cfg(proj, lambda c: c["tasks"]["t2"].__setitem__("verify", []))
    r3, req3 = rules_of(proj, "t2")
    with pytest.raises(CrewError):
        baseline.check_and_update(td, r3, req3, 2)


def test_tightening_is_silent(proj):
    td = proj / "crew" / "t1"
    r, req = rules_of(proj)
    baseline.save(td, baseline.check_and_update(td, r, req, 1))
    edit_cfg(proj, lambda c: c["tasks"]["t1"].__setitem__("review", "double"))
    r2, req2 = rules_of(proj)
    assert baseline.check_and_update(td, r2, req2, 2)["review"] == "double"


def test_round_limit_and_extra_round(proj):
    td = proj / "crew" / "t1"
    r, req = rules_of(proj)
    baseline.save(td, baseline.check_and_update(td, r, req, 1))
    for n in (1, 2):   # 配额按已有轮次目录计（崩溃作废的不算），先造出两轮
        (td / f"round-{n:02d}").mkdir()
    with pytest.raises(CrewError, match="上限"):
        baseline.check_and_update(td, r, req, 3)
    baseline.approve(td, r, req, "extra_round", "用户同意再审一轮")
    base = baseline.check_and_update(td, r, req, 3)
    assert baseline.round_limit(base) == 3


def test_approve_without_change_refused(proj):
    td = proj / "crew" / "t1"
    r, req = rules_of(proj)
    baseline.save(td, baseline.check_and_update(td, r, req, 1))
    with pytest.raises(CrewError, match="没有放宽"):
        baseline.approve(td, r, req, "change", "x")
