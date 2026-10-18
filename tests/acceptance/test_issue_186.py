import re
from pathlib import Path

INSTALL = (Path(__file__).resolve().parents[2] / "sandbox" / "install.sh").read_text(encoding="utf-8")


def test_master_key_kommt_in_install_sh_nicht_mehr_vor():
    assert "LITELLM_MASTER_KEY" not in INSTALL
    assert "Master-Key" not in INSTALL


def test_schreib_token_und_ntfy_werden_weiter_abgefragt():
    for name in ("DASHBOARD_WRITE_TOKEN", "NTFY_URL"):
        assert re.search(r"(?m)^\s*ask_secret /etc/agent-dashboard\.env " + name + r" \"", INSTALL), name


def test_dashboard_abschnitt_bleibt_hinter_dem_schalter():
    abschnitt = INSTALL.split("== Web-Dashboard", 1)[1].split("cat > /etc/systemd/system/agent-dashboard.service", 1)[0]
    assert 'if [ "$DASHBOARD" = 1 ]; then' in abschnitt
    assert abschnitt.count("ask_secret") == 2
    assert abschnitt.rstrip().count("\nfi") >= 1


def test_unit_liest_die_umgebungsdatei_weiter():
    assert "EnvironmentFile=-/etc/agent-dashboard.env" in INSTALL
