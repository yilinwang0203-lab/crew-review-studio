import pytest

from crewlib import reviewers
from crewlib.common import CrewError


def test_codex_uses_path_without_desktop_app(monkeypatch):
    monkeypatch.delenv('CREW_CODEX_BIN', raising=False)
    monkeypatch.setattr(reviewers.shutil, 'which', lambda name: '/usr/local/bin/codex')
    assert reviewers._base('codex') == ['/usr/local/bin/codex']


def test_missing_key_without_macos_keychain_is_clear(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(reviewers.shutil, 'which', lambda name: None)
    with pytest.raises(CrewError, match='DEEPSEEK_API_KEY'):
        reviewers.deepseek_key()
