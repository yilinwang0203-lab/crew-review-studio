from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_demo', ROOT / 'scripts/build_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture
def public_root(tmp_path):
    (tmp_path / 'VERSION').write_text('0.2.0\n')
    (tmp_path / 'studio').mkdir()
    for name in ('studio.html', 'studio-records.css'):
        shutil.copyfile(ROOT / 'studio' / name, tmp_path / 'studio' / name)
    (tmp_path / 'demo.json').write_text(json.dumps({
        'repository': 'https://github.com/example/crew',
        'expires_at': '2026-11-21T03:37:00+00:00',
    }))
    # 即便源目录有私人文件，构建器也不能把它们带进发布包。
    (tmp_path / 'studio/projects.json').write_text('PRIVATE_REGISTRY')
    (tmp_path / 'studio/data').mkdir()
    (tmp_path / 'studio/data/local-records.json').write_text('PRIVATE_RECORDS')
    return tmp_path


def test_public_build_contains_only_demo_assets(public_root):
    out = public_root / '_site'
    assert not demo.build(out, datetime(2026, 10, 22, tzinfo=timezone.utc), public_root)
    assert {p.name for p in out.iterdir()} == {'index.html', '404.html', 'studio-records.css'}
    html = (out / 'index.html').read_text()
    assert 'studio-records.js' not in html
    assert 'source-toggle' not in html
    assert 'PRIVATE_' not in html
    assert '公开演示' in html
    assert 'v0.2.0' in html
    assert 'https://github.com/example/crew' in html


def test_expired_build_removes_previous_demo_assets(public_root):
    out = public_root / '_site'
    demo.build(out, datetime(2026, 10, 22, tzinfo=timezone.utc), public_root)
    assert demo.build(out, datetime(2026, 11, 21, 3, 37, tzinfo=timezone.utc), public_root)
    assert {p.name for p in out.iterdir()} == {'index.html', '404.html'}
    for path in out.iterdir():
        html = path.read_text()
        assert 'data-demo-ended' in html
        assert '<script' not in html
        assert 'task-card' not in html


def test_build_refuses_overwriting_source(public_root):
    with pytest.raises(ValueError, match='_site'):
        demo.build(public_root / 'studio', root=public_root)


def test_build_rejects_symlink_output(public_root):
    target = public_root / 'private'
    target.mkdir()
    (target / 'record.txt').write_text('keep')
    (public_root / '_site').symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='符号链接'):
        demo.build(public_root / '_site', root=public_root)
    assert (target / 'record.txt').read_text() == 'keep'
