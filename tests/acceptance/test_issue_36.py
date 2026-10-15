"""Akzeptanztests für Issue #<N>: MAX_WORKERS prüfen, Slot-Timer aufzählen, Review-Timer im Fehlerfall.

`install.sh` wird nicht ausgeführt (Regel 4: nur Stephan) und `bash` fehlt auf dem
Entwicklungsrechner; geprüft wird deshalb der Text der Datei. Kein tmp_path, kein Netz, kein gh.
"""

import re
from pathlib import Path

INSTALL = (Path(__file__).resolve().parents[2] / "sandbox" / "install.sh").read_text(encoding="utf-8")


def test_max_workers_wird_vor_dem_merken_geprueft():
    m = re.search(r'^.*(?:MAX_WORKERS|WORKERS_WANTED).*\[1-9\]\[0-9\]\*.*$', INSTALL, re.M)
    assert m, "MAX_WORKERS wird nicht als Zahl >= 1 geprüft"
    conf = INSTALL.index("conf_save")
    assert m.start() < conf, "die Prüfung steht hinter dem conf_save; ein Tippfehler wird gemerkt"
    assert "exit 1" in INSTALL[m.start():conf], \
        "bei ungültigem MAX_WORKERS muss der Lauf abbrechen (exit 1)"


def test_slot_timer_werden_aufgezaehlt_statt_1_bis_5():
    assert not re.search(r'seq\s+1\s+5', INSTALL), \
        "Slot-Timer werden über den festen Bereich 1–5 abgeschaltet; Slots ab 6 bleiben aktiv"
    assert "qwen-worker@*.timer" in INSTALL, \
        "die vorhandenen Slot-Timer müssen aus dem System aufgezählt werden"


def test_fehlerfall_schaltet_den_review_timer_ab():
    tail = INSTALL[INSTALL.index('if [ "$ok" = 1 ]'):]
    assert re.search(r'systemctl disable[^\n]*qwen-review\.timer', tail[tail.index("else"):]), \
        "im Fehlerfall (ok != 1) läuft qwen-review.timer weiter, obwohl „Timer bleibt aus“ gemeldet wird"
