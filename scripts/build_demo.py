#!/usr/bin/env python3
"""仅从界面模板构建公开演示；不读取任何项目配置、审查记录或凭据。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def settings(root=ROOT):
    cfg = json.loads((root / 'demo.json').read_text(encoding='utf-8'))
    expires = datetime.fromisoformat(cfg['expires_at'])
    if expires.tzinfo is None:
        raise ValueError('expires_at 必须带时区')
    return cfg, expires


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'界面模板已变，请核对公开构建入口：{old[:80]}')
    return text.replace(old, new, 1)


def build(output, now=None, root=ROOT):
    cfg, expires = settings(root)
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    now = now or datetime.now(timezone.utc)
    output = Path(output).absolute()
    # 只允许在已忽略的产物目录构建，避免误删源码或项目记录。
    if output.is_symlink():
        raise ValueError('输出目录不能是符号链接')
    if output != root.resolve() / '_site':
        raise ValueError('公开构建输出必须是仓库的 _site/ 目录')
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    repo = escape(cfg['repository'], quote=True)
    date = expires.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    expired = now >= expires
    if expired:
        page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crew Studio · 演示已结束</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#171b21;color:#edf0f3;font:16px/1.8 "Avenir Next","PingFang SC",sans-serif}}main{{max-width:580px;padding:36px}}small,a{{color:#bcd9f5}}h1{{font-size:36px}}a{{display:inline-block;border:1px solid #42566b;border-radius:8px;padding:10px 18px;text-decoration:none}}</style>
<main data-demo-ended><small>CREW STUDIO / OPEN SOURCE</small><h1>本次公开演示已结束。</h1>
<p>演示于 {date}（北京时间）到期。项目继续以 MIT 协议开源，可以下载并在本地运行。</p>
<a href="{repo}">在 GitHub 查看开源项目 ↗</a></main></html>'''
    else:
        page = (root / 'studio/studio.html').read_text(encoding='utf-8')
        page = replace_once(page, '<script src="studio-records.js"></script>', '')
        page = replace_once(page, '<button class="btn soft" id="source-toggle">查看记录快照</button>', '')
        page = page.replace('Yilin 的工作区', '开源演示工作区').replace('个人空间', 'MIT OPEN SOURCE')
        page = page.replace('PERSONAL WORKSPACE', 'PUBLIC DEMO').replace('本地演示 · 未连接脚本', '公开演示 · 模拟数据')
        page = replace_once(page, '<title>Crew Studio — 审查室</title>', '<title>Crew Review Studio · 开源交互演示</title>')
        page = replace_once(page, '<span class="version-badge">CONCEPT 03</span>', f'<span class="version-badge">v{escape(version)}</span>')
        banner = f'''<section class="public-demo" aria-label="公开演示说明"><div><span class="public-kicker">OPEN SOURCE · MIT</span><strong>独立审查，共同核对证据。</strong><p>可以体验任务、双审与放行；全部为模拟数据，不上传代码、不调用 AI。</p><small>本期演示开放至 {date}（北京时间）</small></div><a href="{repo}" target="_blank" rel="noopener noreferrer">GitHub 源码 ↗</a></section>'''
        page = replace_once(page, '<div class="content">', '<div class="content">' + banner)
        css = '''<style>.public-demo{display:flex;align-items:center;justify-content:space-between;gap:24px;margin:0 0 32px;padding:22px 24px;border:1px solid #354251;border-left:3px solid #bcd9f5;border-radius:8px;background:#1b222b}.public-demo .public-kicker{display:block;color:#bcd9f5;font:9px var(--mono);letter-spacing:2px;margin-bottom:7px}.public-demo strong{font-size:17px;font-weight:500}.public-demo p{margin:6px 0;color:#a5b1bf;font-size:11px}.public-demo small{color:#8b9aab;font-size:10px}.public-demo>a{white-space:nowrap;padding:10px 15px;border:1px solid #4b5c70;border-radius:5px;color:#d2e5f8;font-size:11px}.public-demo>a:hover{background:#2a3847}@media(max-width:600px){.public-demo{align-items:flex-start;flex-direction:column;gap:13px;padding:18px;margin-bottom:25px}.public-demo strong{font-size:16px}}</style>'''
        page = replace_once(page, '</head>', css + '</head>')
        shutil.copyfile(root / 'studio/studio-records.css', output / 'studio-records.css')
    (output / 'index.html').write_text(page, encoding='utf-8')
    (output / '404.html').write_text(page if expired else '<!doctype html><meta charset="utf-8"><title>页面不存在</title><a href="./">返回 Crew Studio 演示</a>', encoding='utf-8')
    return expired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-expired', action='store_true')
    args = parser.parse_args()
    if args.check_expired:
        _, expires = settings()
        print('true' if datetime.now(timezone.utc) >= expires else 'false')
    else:
        print('演示已结束' if build(ROOT / '_site') else '公开演示已构建到 _site/')


if __name__ == '__main__':
    main()
