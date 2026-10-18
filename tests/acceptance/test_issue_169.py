import re
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
LIB = (_SANDBOX / "install-lib.sh").read_text(encoding="utf-8")
INSTALL = (_SANDBOX / "install.sh").read_text(encoding="utf-8")


def _funktion(text, name):
    treffer = re.search(r"(?ms)^" + re.escape(name) + r"\(\) *\{.*?^\}", text)
    assert treffer, f"Funktion {name} fehlt"
    return treffer.group(0)


def test_logrotate_conf_nennt_beide_logs_und_copytruncate():
    rumpf = _funktion(LIB, "logrotate_conf")
    for teil in ("root/worker.log", "root/audit.jsonl", "copytruncate", "missingok",
                 "notifempty", "compress", "rotate 4", "LOG_ROTATE_SIZE"):
        assert teil in rumpf, teil
    assert "50M" in rumpf


def test_logrotate_conf_prueft_die_groesse():
    rumpf = _funktion(LIB, "logrotate_conf")
    assert "[1-9][0-9]*[kMG]" in rumpf
    assert "exit 1" in rumpf or "return 1" in rumpf


def test_install_sh_installiert_logrotate_und_schreibt_die_datei():
    assert re.search(r"apt-get install(?:[^\n]*\\\n)*[^\n]*\blogrotate\b", INSTALL), "logrotate fehlt in der Paketliste"
    assert "logrotate_conf" in INSTALL
    assert "LOGROTATE_DIR" in INSTALL
    assert "logrotate.d" in INSTALL and "agent-sandbox" in INSTALL


def test_install_sh_bricht_nicht_still_ab_wenn_die_einstellung_scheitert():
    zeilen = [z for z in INSTALL.splitlines() if "logrotate_conf" in z]
    assert zeilen, "logrotate_conf wird in install.sh nicht aufgerufen"
    assert not any("|| true" in z for z in zeilen)
