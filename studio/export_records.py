"""只读导出 crew 文件记录供前端展示；不导入调度器、不执行命令、不判断能否放行。"""
import argparse
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class Reader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.errors = []
        self.seen = {}

    def text(self, path, required=False):
        path = Path(path)
        if not path.exists():
            if required:
                self.errors.append(f"缺少记录：{path.relative_to(self.root)}")
            return None
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            self.errors.append(f"拒绝读取符号链接或项目外记录：{path.name}")
            return None
        try:
            before = path.stat()
            value = path.read_text(encoding='utf-8')
            after = path.stat()
            self.seen[path] = (after.st_mtime_ns, after.st_size)
            if (before.st_mtime_ns, before.st_size) != self.seen[path]:
                self.errors.append(f"读取时记录发生变化：{path.relative_to(self.root)}")
            return value
        except (OSError, UnicodeError) as exc:
            self.errors.append(f"无法读取 {path.relative_to(self.root)}：{type(exc).__name__}")
            return None

    def obj(self, path, required=False):
        value = self.text(path, required)
        if value is None:
            return None
        try:
            obj = json.loads(value)
            if not isinstance(obj, dict):
                raise ValueError('记录根节点不是对象')
            return obj
        except ValueError as exc:
            self.errors.append(f"记录损坏 {Path(path).relative_to(self.root)}：{exc}")
            return None

    def changed(self):
        for p, stamp in self.seen.items():
            try:
                stat = p.stat()
                if stamp != (stat.st_mtime_ns, stat.st_size):
                    self.errors.append(f"导出期间记录变化：{p.relative_to(self.root)}")
            except OSError:
                self.errors.append(f"导出期间记录消失：{p.relative_to(self.root)}")


EXTERNAL_HINTS = (
    ('at capacity', 'OpenAI 模型容量不足'),
    ('too busy', '服务繁忙'),
    ('usage limit', '账户额度用完'),
    ('rate limit', '请求被限流'),
    ('stream disconnected', '网络连接中断'),
    ('connection reset', '网络连接中断'),
    ('econnreset', '网络连接中断'),
    ('network error', '网络错误'),
    ('error sending request', '网络错误'),
    ('timed out', '网络/服务超时'),
    ('insufficient balance', '账户余额不足'),
    ('401 unauthorized', '登录失效'),
)


CRASH_PREFIX = '调度器在审查员运行中途崩溃'


def scan_tail(root, path, limit=2_000_000):
    """只读扫描过程日志末尾找外部故障关键词；不计入读取校验（审查进行中日志会持续增长）。"""
    path = Path(path)
    try:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            return ''
        with open(path, 'rb') as f:
            f.seek(max(0, path.stat().st_size - limit))
            return f.read().decode('utf-8', 'ignore')
    except OSError:
        return ''


def external_failure(text):
    low = (text or '').lower()
    for key, reason in EXTERNAL_HINTS:
        if key in low:
            return reason
    return None


def rulings_from(handoff):
    """交接包里「## 用户裁决…」一节原文（到下一个二级标题为止）。"""
    if not handoff:
        return None
    m = re.search(r'^## 用户裁决[^\n]*\n(.*?)(?=^## |\Z)', handoff, re.S | re.M)
    return m.group(1).strip() if m else None


def one_line(handoff):
    """交接包第一行约定的「一句话：」摘要。"""
    m = re.search(r'^一句话[：:]\s*(.+)$', handoff or '', re.M)
    return m.group(1).strip() if m else None


def revision_log(text):
    """修订版文首「## 修订记录」一节（到下一个二级标题为止）。"""
    m = re.search(r'^## 修订记录.*?(?=^## (?!#)|\Z)', text or '', re.M | re.S)
    return m.group(0).strip() if m else None


def export_draft(r, root, key, conf):
    """设计阶段任务：Codex 写 → Claude 修订 → 公示。不开审查轮。"""
    d = conf.get('draft') if isinstance(conf.get('draft'), dict) else {}
    files, log = [], None
    for f in d.get('files', []) if isinstance(d.get('files'), list) else []:
        if not isinstance(f, dict):
            continue
        orig = r.text(root / f['original'], True) if isinstance(f.get('original'), str) else None
        rev = r.text(root / f['revised'], True) if isinstance(f.get('revised'), str) else None
        log = log or revision_log(rev)
        files.append({'label': f.get('label') or f.get('revised'), 'original_path': f.get('original'), 'revised_path': f.get('revised'), 'original': orig, 'revised': rev})
    req = r.text(root / conf['requirements']) if isinstance(conf.get('requirements'), str) else None
    if not log:
        r.errors.append(f'{key} 的修订版缺少「## 修订记录」')
    return {'id': key, 'name': d.get('title') or key, 'summary': d.get('summary'), 'review': 'draft', 'author': d.get('author', 'Codex'), 'reviser': d.get('reviser', 'Claude'), 'published_at': d.get('published_at'), 'requirements_path': conf.get('requirements'), 'requirements': req, 'revision_log': log, 'files': files, 'max_rounds': None, 'approvals': [], 'rulings': None, 'user_decision': None, 'issues': [], 'rounds': []}


def export_project(project):
    r = Reader(project)
    root = r.root
    cfg = r.obj(root / 'crew/crew.json', True)
    if not cfg or not isinstance(cfg.get('tasks'), dict):
        raise ValueError('无法读取合法的 crew/crew.json 任务配置')
    sums = r.obj(root / 'crew/summaries.json') or {}   # 旧记录的一句话摘要（backfill_summaries.py 补写）
    sum_tasks = sums.get('tasks') if isinstance(sums.get('tasks'), dict) else {}
    sum_issues = sums.get('issues') if isinstance(sums.get('issues'), dict) else {}
    tasks = []
    for key, conf in cfg['tasks'].items():
        if not re.fullmatch(r'[A-Za-z0-9_-]+', key) or not isinstance(conf, dict):
            r.errors.append(f'无法展示的任务配置：{key}')
            continue
        td = root / 'crew' / key
        if conf.get('review') == 'draft':
            tasks.append(export_draft(r, root, key, conf))
            continue
        base = r.obj(td / 'baseline.json') or {}
        issues = r.obj(td / 'issues.json') or {}
        approvals = base.get('approvals', [])
        if not isinstance(approvals, list):
            r.errors.append(f'{key} 的 approvals 类型错误')
            approvals = []
        limit = base.get('max_rounds', cfg.get('max_rounds', 2))
        if type(limit) is not int:
            limit = None
        applied = sum(1 for a in approvals if isinstance(a, dict) and a.get('kind') == 'extra_round' and a.get('applied') is True)
        rounds = []
        if td.exists():
            paths = sorted((p for p in td.iterdir() if p.is_dir() and re.fullmatch(r'round-\d+', p.name)), key=lambda p: int(p.name.split('-')[1]))
        else:
            paths = []
        counted = 0
        for rd in paths:
            n = int(rd.name.split('-')[1])
            rules = r.obj(rd / 'frozen/rules.json', True) or {}
            fp = (r.text(rd / 'fingerprint.txt', True) or '').strip()
            manifest = r.text(rd / 'manifest.txt', True)
            gate = r.obj(rd / 'gate.json')
            release = r.obj(rd / 'released.json')
            required = rules.get('reviewers', ['dsh', 'codex'] if conf.get('review') == 'double' else ['dsh'])
            reviewers = {}
            if not isinstance(required, list) or any(v not in ('dsh', 'codex') for v in required):
                r.errors.append(f'{key}/{rd.name} 审查员配置错误')
                required = []
            for by in required:
                rv = rd / by
                adopted = r.obj(rv / 'adopted.json')
                attempts = []
                aps = sorted((p for p in rv.glob('attempt-*') if p.is_dir() and re.fullmatch(r'attempt-\d+', p.name)), key=lambda p: int(p.name.split('-')[1]))
                for ad in aps:
                    meta = r.obj(ad / 'meta.json')
                    safe_meta = {k: meta.get(k) for k in ['reviewer', 'attempt', 'outcome', 'errors', 'violations', 'exit_code', 'timed_out', 'started', 'ended', 'seconds', 'external_reason', 'external_kind', 'external_retryable']} if meta else None
                    verdict = r.obj(ad / 'verdict.json')
                    artifacts = {name: (ad / name).is_file() for name in ['meta.json','verdict.json','review.md','final.txt','events.jsonl','stderr.log','exit_code'] + (['last-message.txt'] if by == 'codex' else [])}
                    number = int(ad.name.split('-')[1])
                    external = None
                    if meta and meta.get('outcome') == 'external':   # 调度器已判定的外部故障（2026-10-14 起），优先于关键词扫描
                        external = meta.get('external_reason') or '外部故障'
                    elif not (meta and meta.get('outcome') in ('pass', 'reject')):
                        external = external_failure(scan_tail(root, ad / 'stderr.log') + scan_tail(root, ad / 'events.jsonl'))
                    attempts.append({'number': number, 'adopted': bool(adopted and adopted.get('attempt') == number), 'meta': safe_meta, 'verdict': verdict, 'artifacts': artifacts, 'external': external})
                reviewers[by] = {'adopted': adopted, 'attempts': attempts}
            verification = []
            for i, spec in enumerate(rules.get('verify', []), 1):
                vd = rd / 'verify' / f'{i:02d}'
                verification.append({'spec': spec, 'result': r.obj(vd / 'result.json'), 'output': r.text(vd / 'output.log')})
            invalid = r.text(rd / 'INVALID')
            invalid_info = r.obj(rd / 'INVALID.json')
            crashed = invalid_info.get('kind') == 'scheduler_crash' if isinstance(invalid_info, dict) and invalid_info.get('kind') else bool(invalid and invalid.startswith(CRASH_PREFIX))  # 与 crew-review 口径一致：旧数据只认开头
            counted += 0 if (invalid and crashed) else 1   # 调度器崩溃作废的轮次不计入轮次配额
            handoff = r.text(rd / 'frozen/handoff.md')
            rounds.append({'number': n, 'rules': rules, 'fingerprint': fp, 'file_count': len(manifest.splitlines()) if manifest else None, 'handoff': handoff, 'rulings': rulings_from(handoff), 'gate': gate, 'release': release, 'invalid': invalid, 'interrupted': bool(invalid) and crashed, 'counted': counted, 'reviewers': reviewers, 'verification': verification, 'after': r.obj(rd / 'verify/after.json')})
        issues_list = issues.get('issues', [])
        if not isinstance(issues_list, list):
            r.errors.append(f'{key} 的问题台账类型错误')
            issues_list = []
        rulings = next((x['rulings'] for x in reversed(rounds) if x['rulings']), None)
        for it in issues_list:
            if isinstance(it, dict):
                own = it.get('text', {}).get('summary') if isinstance(it.get('text'), dict) else None
                it['summary'] = own if isinstance(own, str) and own.strip() else sum_issues.get(f"{key}:{it.get('id')}")
        summary = next((one_line(x['handoff']) for x in reversed(rounds) if one_line(x['handoff'])), None) or one_line(r.text(td / 'handoff.md')) or sum_tasks.get(key)
        decision = r.obj(td / 'user_decision.json')  # 用户在“需要用户裁决”后做出的决定（独立文件，不改 gate 记录）
        tasks.append({'id': key, 'name': '工具双审自检' if key == 'selfcheck' else key, 'summary': summary if isinstance(summary, str) else None, 'review': conf.get('review'), 'max_rounds': limit + applied if limit is not None else None, 'approvals': approvals, 'rulings': rulings, 'user_decision': decision, 'issues': issues_list, 'rounds': rounds})
    risks = r.obj(root / 'crew/accepted-risks.json') or {}
    accepted = risks.get('risks') if isinstance(risks.get('risks'), list) else []
    r.changed()
    return {'version': 1, 'accepted_risks': accepted, 'generated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'), 'project_name': root.name, 'project_path': str(root), 'mode': 'record_snapshot', 'current_files_verified': False, 'notes': r.errors, 'tasks': tasks}


def write_snapshot(project, output):
    source = Path(project).resolve()
    target = Path(output).resolve()
    if target.is_relative_to(source):
        raise ValueError('输出必须在被审项目以外，防止改变源码或审查产物')
    snapshot = export_project(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as out:
        json.dump(snapshot, out, ensure_ascii=False, indent=2)
        temp_path = out.name
    os.replace(temp_path, target)
    return snapshot, target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', default=str(Path(__file__).parent / 'data/local-records.json'))
    args = parser.parse_args()
    try:
        snapshot, target = write_snapshot(args.project, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    print(f'已只读导出 {len(snapshot["tasks"])} 个登记任务；读取提示 {len(snapshot["notes"])} 条；{target}')


if __name__ == '__main__':
    main()
