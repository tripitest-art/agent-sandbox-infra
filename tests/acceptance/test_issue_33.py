"""Akzeptanztests für Issue #<N>: Einstellungsdatei (conf_save) und Modul-Installation.

Die Shell-Dateien werden als Text geprüft: `bash` fehlt auf dem Entwicklungsrechner, und
`install.sh` führt nur Stephan aus (AGENTS.md, Regel 4). Kein tmp_path, kein Netz, kein gh.
"""

import re
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
LIB = (SANDBOX / "install-lib.sh").read_text(encoding="utf-8")
INSTALL = (SANDBOX / "install.sh").read_text(encoding="utf-8")


def conf_save_body() -> str:
    """Der Rumpf von conf_save(): ab dem Kopf bis zur schließenden Klammer."""
    start = LIB.index("conf_save()")
    return LIB[start:LIB.index("\n}", start)]


def test_conf_save_schreibt_die_einstellungsdatei_mit_modus_600():
    body = conf_save_body()
    assert "umask 022" not in body, "conf_save schreibt die Datei weiterhin weltweit lesbar"
    assert "umask 077" in body, "conf_save muss mit umask 077 schreiben"
    assert re.search(r'chmod\s+600\s+"?\$\{?CONF\}?"?', body), \
        "die Zieldatei muss nach dem Schreiben chmod 600 bekommen"


def test_conf_save_behaelt_schluessel_die_es_nicht_bekommen_hat():
    body = conf_save_body()
    # Wer unbekannte Schlüssel erhalten will, muss die vorhandene Datei lesen, bevor er schreibt.
    reads_old = re.search(r'(grep|sed|awk|cat|read)[^\n]*"\$\{?CONF\}?"', body) \
        or re.search(r'<\s*"\$\{?CONF\}"', body)
    assert reads_old, \
        "conf_save schreibt die Datei nur aus den übergebenen Schlüsseln neu und verliert alles andere"


def test_alle_sandbox_module_werden_mit_einem_befehl_installiert():
    assert re.search(r'install\s+-m\s+\d+\s+"\$SCRIPT_DIR"/\*\.py\s+/root/?', INSTALL), \
        "install.sh muss alle sandbox/*.py in einem Befehl nach /root kopieren"
    chmod_zeilen = "\n".join(l for l in INSTALL.splitlines() if "chmod 755" in l)
    for name in ("worker_tick.py", "review_tick.py", "qwen_webhook.py"):
        einzeln = re.search(r'install\s+-m\s+755\s+"\$SCRIPT_DIR/%s"' % re.escape(name), INSTALL)
        assert f"/root/{name}" in chmod_zeilen or einzeln, \
            f"{name} muss nach der Kopie ausführbar bleiben (755)"
