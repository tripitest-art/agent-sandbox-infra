import re
from pathlib import Path

INSTALL = (Path(__file__).resolve().parents[2] / "sandbox" / "install.sh").read_text(encoding="utf-8")


def _block(text, start_marker, end_marker):
    start = text.index(start_marker)
    return text[start:text.index(end_marker, start)]


def test_url_wird_verdeckt_abgefragt_und_nur_in_der_env_datei_gespeichert():
    assert re.search(r'^ask_secret\s+/etc/ha-status\.env\s+HA_STATUS_URL\b', INSTALL, re.M), \
        "HA_STATUS_URL wird nicht per ask_secret in /etc/ha-status.env abgefragt"
    assert not re.search(r'api/webhook/[0-9A-Za-z_-]{12,}', INSTALL), \
        "eine Webhook-ID steht im Skript (Regel 5: das Repo ist öffentlich)"


def test_service_ist_oneshot_und_liest_die_env_datei():
    unit = _block(INSTALL, "/etc/systemd/system/ha-status.service", "\nUNIT")
    assert "Type=oneshot" in unit
    assert re.search(r'^EnvironmentFile=/etc/ha-status\.env$', unit, re.M), \
        "ohne Strich: fehlt die Datei, soll der Lauf laut und nicht still scheitern"
    assert re.search(r'^ExecStart=.*flock -n /run/ha-status\.lock .*python3 /root/ha_status\.py$', unit, re.M)
    assert "append:/root/worker.log" in unit, "Fehler müssen im Log sichtbar werden (Regel 7)"
    assert "HA_STATUS_URL=" not in unit, "die URL darf nicht in der Unit stehen"


def test_timer_laeuft_alle_zwei_minuten():
    unit = _block(INSTALL, "/etc/systemd/system/ha-status.timer", "\nUNIT")
    assert re.search(r'^OnUnitActiveSec=2min$', unit, re.M)
    assert re.search(r'^OnBootSec=\S+$', unit, re.M)
    assert "WantedBy=timers.target" in unit


def test_timer_nur_mit_url_aktiv_und_sonst_aus():
    abschnitt = INSTALL[INSTALL.index('echo "== Status-Bericht"'):]
    cond = abschnitt.index("HA_STATUS_URL=.")
    enable = abschnitt.index("systemctl enable ha-status.timer")
    disable = abschnitt.index("systemctl disable --now ha-status.timer")
    assert cond < enable < disable, "enable gehört in den Zweig mit URL, disable in den else-Zweig"
    assert "else" in abschnitt[enable:disable]


def test_status_timer_haengt_nicht_an_der_pruefung_der_worker():
    # Hinter „== Prüfung“ entscheidet ok über Worker- und Review-Timer; ha-status darf dort nicht stehen.
    pruefung = INSTALL[INSTALL.index('echo "== Prüfung"'):]
    assert "ha-status" not in pruefung
    assert INSTALL.index('echo "== Status-Bericht"') < INSTALL.index('echo "== Prüfung"')


def test_changelog_nennt_den_status_bericht():
    changelog = (Path(__file__).resolve().parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "ha-status" in unreleased
