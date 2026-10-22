"""把只读记录快照整理成给人看的 Word 审查报告（不改被审项目）。"""
import tempfile
from pathlib import Path

import md2docx  # noqa: E402

GATE = {'released': '放行', 'rejected': '退回', 'invalid': '失效', 'pending': '等待', 'needs_user': '待用户裁决'}
VERDICT = {'pass': '通过', 'reject': '退回', 'incomplete': '未完成'}
ISSUE = {'open': '未关闭', 'closing': '待放行确认', 'closed': '已关闭'}


def cell(s):
    return str(s if s not in (None, '') else '-').replace('|', '／').replace('\n', ' ')


def review_cell(rv):
    ad = (rv or {}).get('adopted')
    if not ad:
        return '未采用'
    a = next((x for x in rv['attempts'] if x['number'] == ad.get('attempt')), None)
    v = (a or {}).get('verdict') or {}
    n = len(v.get('blockers') or [])
    return f"{VERDICT.get(ad.get('verdict'), ad.get('verdict'))}" + (f'（{n} 条问题）' if n else '')


def verify_cell(r):
    res = [x.get('result') or {} for x in r['verification']]
    if not res:
        return '-'
    ok = all(x.get('exit_code') == 0 and not x.get('timed_out') for x in res)
    return '通过' if ok else '未通过'


def build_md(snap):
    out = ['# crew-review 审查报告', '',
           f"项目：{snap['project_path']}", f"导出时间：{snap['generated_at']}", '',
           '> 本报告由审查记录整理而成；当前源码是否与放行版本一致，以 crew.py gate 为准。', '']
    if snap['notes']:
        out += ['## 读取提示', ''] + [f'- {n}' for n in snap['notes']] + ['']
    for t in snap['tasks']:
        rounds = t['rounds']
        last = rounds[-1] if rounds else None
        status = GATE.get(((last or {}).get('gate') or {}).get('status'), '尚未裁决') if last else '尚未送审'
        if last and last.get('invalid'):
            status = '失效'
        iss = t['issues']
        cnt = {k: sum(1 for i in iss if i.get('status') == k) for k in ISSUE}
        out += [f"## 任务：{t['name']}（{t['id']}）", '',
                '| 项目 | 内容 |', '| --- | --- |',
                f"| 审查级别 | {'双审（dsh + Codex）' if t['review'] == 'double' else '单审（dsh）'} |",
                f"| 已送审轮次 | {len(rounds)} 轮（上限 {cell(t['max_rounds'])}） |",
                f"| 当前结果 | 第 {last['number'] if last else '-'} 轮：{status} |",
                f"| 问题 | 共 {len(iss)} 条：已关闭 {cnt['closed']}，待放行确认 {cnt['closing']}，未关闭 {cnt['open']} |", '']
        out += ['### 各轮结果', '', '| 轮次 | 结果 | dsh | Codex | 自动验证 |', '| --- | --- | --- | --- | --- |']
        for r in rounds:
            g = '失效' if r.get('invalid') else GATE.get((r.get('gate') or {}).get('status'), '未裁决')
            out.append(f"| 第 {r['number']} 轮 | {g} | {review_cell(r['reviewers'].get('dsh'))} | "
                       f"{review_cell(r['reviewers'].get('codex')) if 'codex' in r['reviewers'] else '-'} | {verify_cell(r)} |")
        out.append('')
        if iss:
            out += ['### 问题清单', '']
            for i in sorted(iss, key=lambda i: (i.get('status') != 'open', i.get('round', 0))):
                tx = i.get('text') or {}
                out += [f"#### {i['id']}　{ISSUE.get(i.get('status'), i.get('status'))}　（{i.get('raised_by')} 第 {i.get('round')} 轮提出）", '',
                        f"- 位置：{cell(tx.get('where'))}", f"- 触发：{cell(tx.get('trigger'))}",
                        f"- 结果：{cell(tx.get('actual'))}", f"- 依据：{cell(tx.get('why'))}"]
                if i.get('replies'):
                    rep = i['replies'][-1]
                    out.append(f"- 最后回复（第 {rep.get('round')} 轮）：{cell('；'.join(rep.get('lines') or []))}")
                out.append('')
        if last:
            sugg = []
            for by, rv in last['reviewers'].items():
                ad = (rv or {}).get('adopted') or {}
                a = next((x for x in rv['attempts'] if x['number'] == ad.get('attempt')), None)
                v = (a or {}).get('verdict') or {}
                sugg += [('加固项', by, h.get('where'), h.get('trigger', '') + '；' + h.get('note', '')) for h in v.get('hardening') or []]
                sugg += [('改进建议', by, p.get('where'), p.get('note')) for p in v.get('polish') or []]
            if sugg:
                out += [f"### 第 {last['number']} 轮的不阻断建议（只记账，不影响放行）", '',
                        '| 类型 | 提出者 | 位置 | 说明 |', '| --- | --- | --- | --- |']
                out += [f'| {k} | {by} | {cell(w)} | {cell(n)} |' for k, by, w, n in sugg]
                out += ['', '历轮完整建议见项目内 crew/ledger.md。', '']
    return '\n'.join(out)


def build_docx(snap, out_path):
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / 'report.md'
        md.write_text(build_md(snap), encoding='utf-8')
        md2docx.convert(str(md), str(out_path))
    return out_path
