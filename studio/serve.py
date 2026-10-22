"""本地预览服务：静态托管本目录；支持多个项目，每次请求记录时先只读重新导出，刷新即最新。

项目清单 = 本目录 projects.json（[{"name": ..., "path": ...}]）+ 命令行 --project（可重复）。
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import export_records
import report

HERE = Path(__file__).resolve().parent
DATA = '/data/local-records.json'
REGISTRY = HERE / 'projects.json'
DEFAULT_PROJECT = HERE.parent / 'examples/demo-project'


def load_projects(extra=()):
    """读取项目清单；同一路径只保留一次；名称重复时加序号。"""
    items = []
    try:
        raw = json.loads(REGISTRY.read_text(encoding='utf-8')) if REGISTRY.exists() else []
    except (OSError, ValueError):
        raw = []
    for x in raw if isinstance(raw, list) else []:
        if isinstance(x, dict) and isinstance(x.get('path'), str):
            path = Path(x['path']).expanduser()
            items.append((x.get('name'), path if path.is_absolute() else REGISTRY.parent / path))
    for p in extra:
        items.append((None, str(p)))
    if not items:
        items.append((None, str(DEFAULT_PROJECT)))
    out, seen, names = [], set(), set()
    for name, path in items:
        resolved = Path(path).expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        name = name or resolved.name
        base, i = name, 2
        while name in names:
            name, i = f'{base}-{i}', i + 1
        names.add(name)
        out.append({'name': name, 'path': resolved})
    return out


def last_activity(path):
    """项目最近一次审查活动时间（crew.json 与各任务 round 目录的修改时间）。"""
    crew = Path(path) / 'crew'
    stamps = []
    for p in [crew / 'crew.json', *crew.glob('*/round-*'), *crew.glob('*/round-*/*')]:
        try:
            stamps.append(p.stat().st_mtime)
        except OSError:
            pass
    return max(stamps) if stamps else 0


class Handler(http.server.SimpleHTTPRequestHandler):
    extra = ()

    def projects(self):
        return load_projects(self.extra)

    def pick(self, query):
        """按 ?project=名称 选项目；不给时取最近有活动的项目。"""
        projects = self.projects()
        name = (parse_qs(query).get('project') or [''])[0]
        for p in projects:
            if p['name'] == name:
                return p
        return max(projects, key=lambda p: last_activity(p['path']))

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ('/', '/index.html'):  # 旧版页面已删，根地址直接进 studio
            self.send_response(302)
            self.send_header('Location', '/studio.html' + (f'?{url.query}' if url.query else ''))
            self.end_headers()
            return
        if url.path == '/status':
            return self.send_json({'running': (HERE / 'run.flag').exists()})
        if url.path == '/projects':
            rows = [{'name': p['name'], 'path': str(p['path']), 'updated': last_activity(p['path']),
                     'ok': (p['path'] / 'crew/crew.json').is_file()} for p in self.projects()]
            rows.sort(key=lambda r: -r['updated'])
            return self.send_json(rows)
        if url.path == '/records':
            p = self.pick(url.query)
            try:
                snap = export_records.export_project(p['path'])
            except Exception as exc:
                return self.send_json({'error': f'{p["name"]} 导出失败：{exc}'}, 500)
            snap['project_key'] = p['name']
            return self.send_json(snap)
        if url.path == '/report.docx':
            return self.send_report(self.pick(url.query))
        if url.path == DATA:  # 兼容旧页面：导出“最近有活动”的项目
            try:
                export_records.write_snapshot(self.pick(url.query)['path'], HERE / 'data/local-records.json')
            except Exception as exc:  # 导出失败时仍返回上一份快照，前端自行提示
                self.log_message('导出失败：%s', exc)
        super().do_GET()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_report(self, project):
        try:
            snap = export_records.export_project(project['path'])
            out = HERE / 'output' / f'crew-review-report-{project["name"]}.docx'
            out.parent.mkdir(exist_ok=True)
            body = report.build_docx(snap, out).read_bytes()
        except Exception as exc:
            return self.send_error(500, f'report failed: {exc}')
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        self.send_header('Content-Disposition', "attachment; filename*=UTF-8''crew-review-report.docx")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', action='append', default=[], help='追加一个项目（可重复）；也可写进 projects.json')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    Handler.extra = tuple(args.project)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), functools.partial(Handler, directory=str(HERE)))
    print(f'http://127.0.0.1:{args.port}/studio.html  项目：{", ".join(p["name"] for p in load_projects(Handler.extra))}')
    server.serve_forever()


if __name__ == '__main__':
    main()
