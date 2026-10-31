import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "egress-filter.md"
_README = (_ROOT / "README.md").read_text(encoding="utf-8")


def _text() -> str:
    assert _DOC.is_file(), "docs/egress-filter.md fehlt"
    return _DOC.read_text(encoding="utf-8")


def test_abschnitte():
    t = _text()
    for h in ("## Zielbild", "## Regeln", "## Rückbau", "## Grenzen"):
        assert h in t, f"{h} fehlt in docs/egress-filter.md"


def test_ziele_genannt():
    t = _text()
    for s in ("CT 113", "CT 110", "CT 101", "CT 103", "ntfy", "8099", "api.github.com", "files.pythonhosted.org"):
        assert s in t, f"{s} fehlt in docs/egress-filter.md"


def test_keine_geheimnisse_im_text():
    t = _text()
    assert not re.search(r"(ghp_|github_pat_|sk-)[A-Za-z0-9_-]{10,}", t)
    assert not re.search(r"\b[A-Za-z0-9+/=_-]{40,}\b", t.replace("`", " ")), "langer Token-ähnlicher Text"
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", t), "IP-Adresse im Text"


def test_readme_verweist_darauf():
    assert "docs/egress-filter.md" in _README
