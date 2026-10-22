"""todo4 四项改进串起来的完整流程。"""
from conftest import HANDOFF, crew
from crewlib import accepted_risks
from crewlib.common import read_json


def test_todo4_end_to_end(proj, tmp_path):
    td = proj / "crew" / "t1"
    env = {"FAKE_SEQ": "ext_capacity,reject", "FAKE_SEQ_FILE": str(tmp_path / "seq"),
           "CREW_EXTERNAL_BACKOFF_SCALE": "0"}
    crew(proj, "snapshot", "t1")
    code, out = crew(proj, "review", "t1", "--by", "dsh", env_extra=env)      # 外部故障 → 自动重试 → 退回
    assert code == 0 and "外部故障" in out
    assert read_json(td / "round-01" / "dsh" / "attempt-01" / "meta.json")["outcome"] == "external"
    (td / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 不改：用户会裁决\n", encoding="utf-8")
    code, out = crew(proj, "snapshot", "t1")                                 # 忘了 gate：自动补
    assert code == 0 and "自动 gate" in out
    ad = td / "round-02" / "dsh" / "attempt-01"                              # round-02 调度器崩溃：免费
    ad.mkdir(parents=True)
    (ad / "events.jsonl").write_text('{"type": "session"}\n', encoding="utf-8")
    assert crew(proj, "gate", "t1")[0] == 1
    (td / "handoff.md").write_text(HANDOFF + "dsh:R01:B1 不改：用户会裁决\n", encoding="utf-8")
    assert crew(proj, "snapshot", "t1")[0] == 0                              # round-03 = 计费第 2 轮
    crew(proj, "review", "t1", "--by", "dsh", env_extra={"FAKE_MODE": "reject"})
    assert crew(proj, "gate", "t1")[0] == 3                                  # 到上限，需要用户裁决
    code, out = crew(proj, "decide", "t1", "--accept", "--reason", "用户接受：演示数据")
    assert code == 0 and "t1:dsh:R03:B1" in out
    ids = {r["id"] for r in accepted_risks.load(proj)["risks"]}
    assert "t1:dsh:R03:B1" in ids
    assert crew(proj, "snapshot", "t2")[0] == 0                              # 其他任务冻结这些风险
    for rev in ("dsh", "codex"):
        p = (proj / "crew" / "t2" / "round-01" / "frozen" / f"prompt-{rev}.md").read_text(encoding="utf-8")
        assert "t1:dsh:R03:B1" in p
    assert not (td / "round-03" / "released.json").exists()
