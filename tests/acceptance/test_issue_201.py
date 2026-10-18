import re
from pathlib import Path

_INSTALL = Path(__file__).resolve().parents[2] / "sandbox" / "install.sh"


def test_install_sh_liest_takt_und_review_takt_aus_conf():
    content = _INSTALL.read_text(encoding="utf-8")
    assert re.search(r"TAKT=\$\{TAKT:-\$\(conf_get TAKT\)\}", content)
    assert re.search(r"REVIEW_TAKT=\$\{REVIEW_TAKT:-\$\(conf_get REVIEW_TAKT\)\}", content)


def test_install_sh_speichert_takt_und_review_takt_in_conf_save():
    content = _INSTALL.read_text(encoding="utf-8")
    save_match = re.search(r"conf_save\s+([^)\n]+)", content)
    assert save_match is not None
    args = save_match.group(1).split()
    assert "TAKT" in args
    assert "REVIEW_TAKT" in args
