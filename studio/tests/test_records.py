import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_records import export_project


class RecordSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.write('crew/crew.json', {'tasks': {'selfcheck': {'review': 'double'}}, 'max_rounds': 2})
        self.write('crew/selfcheck/baseline.json', {'max_rounds': 2, 'approvals': []})
        self.write('crew/selfcheck/issues.json', {'issues': [{'id': 'codex:R01:B1', 'status': 'closing'}]})
        self.write('crew/selfcheck/round-01/frozen/rules.json', {'reviewers': ['dsh','codex'], 'verify': []})
        self.write('crew/selfcheck/round-01/fingerprint.txt', 'a'*64)
        self.write('crew/selfcheck/round-01/manifest.txt', 'a'*64+'  app.py\n')

    def write(self, name, value):
        p = self.root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value) if isinstance(value, (list,dict)) else value)

    def files(self):
        return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}

    def test_export_is_read_only_and_preserves_closing(self):
        before = self.files()
        snapshot = export_project(self.root)
        self.assertEqual(before, self.files())
        self.assertFalse(snapshot['current_files_verified'])
        self.assertEqual('selfcheck',snapshot['tasks'][0]['id'])
        self.assertEqual('closing',snapshot['tasks'][0]['issues'][0]['status'])
        self.assertIsNone(snapshot['tasks'][0]['rounds'][0]['gate'])

    def test_incomplete_attempt_cannot_become_adopted_pass(self):
        self.write('crew/selfcheck/round-01/dsh/attempt-01/meta.json', {'outcome':'incomplete','timed_out':True})
        record = export_project(self.root)['tasks'][0]['rounds'][0]
        self.assertIsNone(record['reviewers']['dsh']['adopted'])
        self.assertEqual('incomplete',record['reviewers']['dsh']['attempts'][0]['meta']['outcome'])
        self.assertIsNone(record['release'])

    def test_corrupt_gate_is_reported_without_fabricated_release(self):
        self.write('crew/selfcheck/round-01/gate.json', '{broken')
        snapshot = export_project(self.root)
        self.assertTrue(any('gate.json' in s for s in snapshot['notes']))
        self.assertIsNone(snapshot['tasks'][0]['rounds'][0]['gate'])
        self.assertIsNone(snapshot['tasks'][0]['rounds'][0]['release'])

    def test_missing_frozen_evidence_is_visible(self):
        (self.root/'crew/selfcheck/round-01/fingerprint.txt').unlink()
        snapshot=export_project(self.root)
        self.assertTrue(any('fingerprint.txt' in s for s in snapshot['notes']))
        self.assertEqual('',snapshot['tasks'][0]['rounds'][0]['fingerprint'])

    def test_extra_round_requires_recorded_applied_approval(self):
        self.write('crew/selfcheck/baseline.json', {'max_rounds':2,'approvals':[{'kind':'extra_round','applied':False}]})
        self.assertEqual(2,export_project(self.root)['tasks'][0]['max_rounds'])
        self.write('crew/selfcheck/baseline.json', {'max_rounds':2,'approvals':[{'kind':'extra_round','applied':True}]})
        self.assertEqual(3,export_project(self.root)['tasks'][0]['max_rounds'])

    def test_cli_refuses_writing_into_reviewed_project(self):
        before=self.files()
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'export_records.py'),'--project',str(self.root),'--output',str(self.root/'crew/browser.json')],capture_output=True,text=True)
        self.assertNotEqual(0,result.returncode)
        self.assertEqual(before,self.files())


    def test_external_failure_is_labeled_not_a_verdict(self):
        self.write('crew/selfcheck/round-01/codex/attempt-01/meta.json', {'outcome': 'incomplete', 'exit_code': 1})
        self.write('crew/selfcheck/round-01/codex/attempt-01/events.jsonl', '{"message":"Selected model is at capacity. Please try a different model."}\n')
        self.write('crew/selfcheck/round-01/dsh/attempt-01/meta.json', {'outcome': 'reject'})
        self.write('crew/selfcheck/round-01/dsh/attempt-01/stderr.log', 'rate limit somewhere in a real reject')
        rv = export_project(self.root)['tasks'][0]['rounds'][0]['reviewers']
        self.assertEqual('OpenAI 模型容量不足', rv['codex']['attempts'][0]['external'])
        self.assertIsNone(rv['dsh']['attempts'][0]['external'])  # 有正式结论就不算外部故障

    def test_export_external_meta_priority(self):
        self.write('crew/selfcheck/round-01/codex/attempt-01/meta.json', {'outcome': 'external', 'external_reason': '网络连接中断', 'external_kind': 'network', 'external_retryable': True})
        self.write('crew/selfcheck/round-01/codex/attempt-01/stderr.log', 'at capacity')
        a = export_project(self.root)['tasks'][0]['rounds'][0]['reviewers']['codex']['attempts'][0]
        self.assertEqual('网络连接中断', a['external'])
        self.assertEqual('network', a['meta']['external_kind'])

    def test_export_accepted_risks_and_structured_invalid(self):
        snap = export_project(self.root)
        self.assertEqual([], snap['accepted_risks'])
        risk = {'id': 't1:dsh:R01:B1', 'text': {'where': 'a.py:1'}, 'reason': '用户接受', 'source': {'task': 't1', 'issue_id': 'dsh:R01:B1'}, 'at': 'x'}
        self.write('crew/accepted-risks.json', {'risks': [risk]})
        self.write('crew/selfcheck/round-01/INVALID', '审查期间提到中途崩溃字样的写入违规')
        self.write('crew/selfcheck/round-01/INVALID.json', {'kind': 'other'})
        before = self.files()
        snap = export_project(self.root)
        self.assertEqual(before, self.files())
        self.assertEqual([risk], snap['accepted_risks'])
        self.assertFalse(snap['tasks'][0]['rounds'][0]['interrupted'])   # 结构化记录优先于文字
        self.write('crew/selfcheck/round-01/INVALID.json', {'kind': 'scheduler_crash'})
        self.assertTrue(export_project(self.root)['tasks'][0]['rounds'][0]['interrupted'])

    def test_rulings_section_and_interrupted_round(self):
        self.write('crew/selfcheck/round-01/frozen/handoff.md', '任务：x\n\n## 用户裁决（2026-10-13）\n1. 没有折扣\n2. 有人值守\n\n## 第 4 轮回复\n- 已改\n')
        self.write('crew/selfcheck/round-01/INVALID', '调度器在审查员运行中途崩溃（dsh/attempt-01），本轮作废')
        snap = export_project(self.root)
        t = snap['tasks'][0]
        self.assertEqual('1. 没有折扣\n2. 有人值守', t['rulings'])
        self.assertTrue(t['rounds'][0]['interrupted'])

    def test_no_rulings_is_none(self):
        self.write('crew/selfcheck/round-01/frozen/handoff.md', '任务：x\n')
        self.assertIsNone(export_project(self.root)['tasks'][0]['rulings'])

    def test_user_decision_is_exported(self):
        self.write('crew/selfcheck/user_decision.json', {'round': 1, 'decision': 'accept', 'reason': '用户选 A'})
        self.assertEqual('accept', export_project(self.root)['tasks'][0]['user_decision']['decision'])

    def test_legacy_crash_text_must_start_with_prefix(self):
        self.write('crew/selfcheck/round-01/INVALID', 'dsh attempt-01 审查期间发生写入/升权：调度器在审查员运行中途崩溃')
        self.assertFalse(export_project(self.root)['tasks'][0]['rounds'][0]['interrupted'])

    def test_counted_rounds_skip_crash_rounds(self):
        for n in (2, 3):
            self.write(f'crew/selfcheck/round-0{n}/frozen/rules.json', {'reviewers': ['dsh','codex'], 'verify': []})
        self.write('crew/selfcheck/round-01/INVALID', '调度器在审查员运行中途崩溃（dsh/attempt-01），本轮作废')
        self.write('crew/selfcheck/round-02/INVALID', '送审后源码被改，本轮失效')
        self.write('crew/selfcheck/round-02/INVALID.json', {'kind': 'other'})
        rounds = export_project(self.root)['tasks'][0]['rounds']
        self.assertEqual([0, 1, 2], [r['counted'] for r in rounds])

    def test_one_line_summaries(self):
        self.write('crew/selfcheck/round-01/frozen/handoff.md', '一句话：给审查工具加摘要\n任务：x\n')
        self.write('crew/selfcheck/issues.json', {'issues': [
            {'id': 'dsh:R01:B1', 'status': 'open', 'text': {'summary': '审查员自己写的', 'where': 'a'}},
            {'id': 'dsh:R01:B2', 'status': 'open', 'text': {'where': 'b'}},
            {'id': 'dsh:R01:B3', 'status': 'open', 'text': {'where': 'c'}}]})
        self.write('crew/summaries.json', {'tasks': {'selfcheck': '补写的任务摘要'},
                                           'issues': {'selfcheck:dsh:R01:B1': '不该用', 'selfcheck:dsh:R01:B2': '补写的问题摘要'}})
        before = self.files()
        t = export_project(self.root)['tasks'][0]
        self.assertEqual(before, self.files())
        self.assertEqual('给审查工具加摘要', t['summary'])                 # 交接包自带优先
        self.assertEqual(['审查员自己写的', '补写的问题摘要', None], [i['summary'] for i in t['issues']])
        self.write('crew/selfcheck/round-01/frozen/handoff.md', '任务：x\n')
        self.assertEqual('补写的任务摘要', export_project(self.root)['tasks'][0]['summary'])


if __name__ == '__main__':
    unittest.main()
