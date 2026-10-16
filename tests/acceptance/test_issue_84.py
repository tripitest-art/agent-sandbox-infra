from pathlib import Path

_CFG = Path(__file__).resolve().parents[2] / ".github" / "dependabot.yml"


def _text() -> str:
    assert _CFG.is_file(), ".github/dependabot.yml fehlt"
    return _CFG.read_text(encoding="utf-8")


def test_version_und_okosystem():
    t = _text()
    assert "version: 2" in t
    assert 'package-ecosystem: "github-actions"' in t
    assert 'directory: "/"' in t


def test_wochentakt_und_limit():
    t = _text()
    assert 'interval: "weekly"' in t
    assert "open-pull-requests-limit: 2" in t


def test_kein_pip_okosystem():
    assert "pip" not in _text()
