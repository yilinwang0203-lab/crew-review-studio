import pytest

from crewlib import reviewers
from crewlib.common import CrewError

from conftest import crew


def test_codex_uses_path_without_desktop_app(monkeypatch):
    monkeypatch.delenv('CREW_CODEX_BIN', raising=False)
    monkeypatch.setattr(reviewers.shutil, 'which', lambda name: '/usr/local/bin/codex')
    assert reviewers._base('codex') == ['/usr/local/bin/codex']


def test_missing_key_without_macos_keychain_is_clear(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(reviewers.shutil, 'which', lambda name: None)
    with pytest.raises(CrewError, match='DEEPSEEK_API_KEY'):
        reviewers.deepseek_key()


@pytest.mark.parametrize('mode,gate_code', [('hardening_no_trigger', 0), ('prefixed_id', 1)])
def test_gate_rechecks_normalized_raw_verdict(proj, mode, gate_code):
    assert crew(proj, 'snapshot', 't1')[0] == 0
    code, output = crew(proj, 'review', 't1', '--by', 'dsh', env_extra={'FAKE_MODE': mode})
    assert code == 0 and '已自动规整' in output
    assert crew(proj, 'gate', 't1')[0] == gate_code
