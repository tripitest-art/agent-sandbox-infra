import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "backup.md"
_README = (_ROOT / "README.md").read_text(encoding="utf-8")


def _text() -> str:
    assert _DOC.is_file(), "docs/backup.md fehlt"
    return _DOC.read_text(encoding="utf-8")


def test_abschnitte_und_pfade():
    t = _text()
    assert "## Sichern" in t and "## Wiederherstellen" in t
    for pfad in ("/etc/agent-sandbox.conf", "/etc/agent-projects.json", "/etc/qwen-worker.env",
                 "/etc/qwen-reviewer.env", "/etc/qwen-webhook.env"):
        assert pfad in t, f"{pfad} fehlt in docs/backup.md"


def test_keine_geheimnisse_im_text():
    t = _text()
    assert not re.search(r"(ghp_|github_pat_|sk-)[A-Za-z0-9_-]{10,}", t)
    assert not re.search(r"\b[A-Za-z0-9+/=_-]{40,}\b", t.replace("`", " ")), "langer Token-ähnlicher Text"
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", t), "IP-Adresse im Text"


def test_readme_verweist_darauf():
    assert "docs/backup.md" in _README
