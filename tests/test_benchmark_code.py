"""Eigene Tests zu den Code-Aufgaben und der abgesicherten Prüfung (#335).

Die Akzeptanztests decken Schnittstelle und Grenzen ab; hier kommen die Fälle dazu, die das Issue
zusätzlich verlangt: ein Kindprozess des Modellcodes überlebt das Zeitlimit nicht, die Sonderfälle
von `extract_code` und je zwei weitere falsche Lösungen pro Aufgabe. Kein Test fasst ein echtes
Netz an: die Socket-Sperre wird nur daraufhin geprüft, dass der Zugriff scheitert.
"""
from pathlib import Path

import pytest

from sandbox.core import benchmark_code

AUFGABEN_IDS = ["dauer_parsen", "rate_limiter", "wartet_auf"]

WEITERE_FALSCH = {
    "dauer_parsen": [
        # prüft die Reihenfolge nicht: doppelte Einheiten und verdrehte Reihenfolge rutschen durch
        "import re\n"
        "def parse_dauer(text):\n"
        "    if not text:\n"
        "        raise ValueError(text)\n"
        "    werte = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}\n"
        "    gesamt, pos = 0, 0\n"
        "    for treffer in re.finditer(r'(\\d+)([dhms])', text):\n"
        "        if treffer.start() != pos:\n"
        "            raise ValueError(text)\n"
        "        gesamt += int(treffer.group(1)) * werte[treffer.group(2)]\n"
        "        pos = treffer.end()\n"
        "    if pos != len(text):\n"
        "        raise ValueError(text)\n"
        "    return gesamt\n",
        # schneidet Leerzeichen ab, statt sie als ungültig abzulehnen
        "import re\n"
        "def parse_dauer(text):\n"
        "    text = text.strip()\n"
        "    if not text:\n"
        "        raise ValueError(text)\n"
        "    m = re.fullmatch(r'(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?', text)\n"
        "    if m is None or not any(m.groups()):\n"
        "        raise ValueError(text)\n"
        "    d, h, mi, s = (int(x or 0) for x in m.groups())\n"
        "    return d * 86400 + h * 3600 + mi * 60 + s\n",
    ],
    "rate_limiter": [
        # lässt das Fenster nie ablaufen
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        if max_aufrufe < 1 or fenster_s <= 0:\n"
        "            raise ValueError('x')\n"
        "        self.m, self.u, self.n = max_aufrufe, uhr, 0\n"
        "    def erlaubt(self):\n"
        "        jetzt = self.u()\n"
        "        if self.n < self.m:\n"
        "            self.n += 1\n"
        "            return True\n"
        "        return False\n",
        # fragt die übergebene Uhr gar nicht, sondern die Systemzeit
        "import time\n"
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        if max_aufrufe < 1 or fenster_s <= 0:\n"
        "            raise ValueError('x')\n"
        "        self.m, self.f, self.t = max_aufrufe, fenster_s, []\n"
        "    def erlaubt(self):\n"
        "        jetzt = time.monotonic()\n"
        "        self.t = [x for x in self.t if x > jetzt - self.f]\n"
        "        if len(self.t) >= self.m:\n"
        "            return False\n"
        "        self.t.append(jetzt)\n"
        "        return True\n",
    ],
    "wartet_auf": [
        # vergleicht die Zeilenanfänge Groß-/Kleinschreibung-abhängig
        "import re\n"
        "def parse_wartet_auf(text):\n"
        "    out, im_block = set(), False\n"
        "    for z in text.splitlines():\n"
        "        if z.lstrip().startswith('```'):\n"
        "            im_block = not im_block\n"
        "            continue\n"
        "        if not im_block and z.lstrip().startswith('Wartet auf:'):\n"
        "            out.update(int(n) for n in re.findall(r'#(\\d+)', z))\n"
        "    return sorted(out)\n",
        # nimmt auch Zahlen ohne '#'
        "import re\n"
        "def parse_wartet_auf(text):\n"
        "    out, im_block = set(), False\n"
        "    for z in text.splitlines():\n"
        "        if z.lstrip().startswith('```'):\n"
        "            im_block = not im_block\n"
        "            continue\n"
        "        if not im_block and z.lstrip().lower().startswith('wartet auf:'):\n"
        "            out.update(int(n) for n in re.findall(r'\\d+', z))\n"
        "    return sorted(out)\n",
    ],
}


def _aufgabe(aufgabe_id: str) -> dict:
    return {t["id"]: t for t in benchmark_code.CODE_TASKS}[aufgabe_id]


def _antwort(code: str) -> str:
    return "Meine Lösung:\n\n```python\n" + code + "\n```\n\nViel Erfolg!"


def _beendet(pid: int) -> bool:
    """True, wenn der Prozess weg ist; ein Zombie zählt als beendet."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    return stat.rsplit(")", 1)[1].split()[0] == "Z"


def test_gruende_sind_fest():
    assert benchmark_code.GRUND_KEIN_CODE == "kein Code"
    assert benchmark_code.GRUND_ZEITLIMIT == "Zeitlimit"
    assert benchmark_code.GRUND_NETZ == "Netzzugriff verboten"
    assert benchmark_code.GRUND_FEHLGESCHLAGEN == "Prüfung fehlgeschlagen"


def test_extract_code_mehrere_bloecke_und_python_gross_geschrieben():
    antwort = "```python\nx = 1\n```\n```Python\ndef f():\n    return 1\n```\n```python\ndef g():\n    pass\n```"
    assert benchmark_code.extract_code(antwort) == "def f():\n    return 1"


def test_extract_code_nicht_geschlossener_zaun_ist_none():
    assert benchmark_code.extract_code("```python\ndef f():\n    return 1\n") is None
    assert benchmark_code.extract_code("```\nclass A:\n    pass\n") is None


def test_extract_code_ueberspringt_fremde_sprache_und_findet_danach_python():
    antwort = "```bash\ndef f(): echo\n```\n```python\ndef f():\n    pass\n```"
    assert benchmark_code.extract_code(antwort) == "def f():\n    pass"


def test_extract_code_wirft_bei_kaputter_eingabe_nie():
    for antwort in (None, 42, b"```python\ndef f(): pass\n```", "```python\n```"):
        assert benchmark_code.extract_code(antwort) is None


def test_kindprozess_ueberlebt_das_zeitlimit_nicht(tmp_path):
    pid_datei = tmp_path / "kind.pid"
    code = (
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "kind = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pid_datei)!r}, 'w').write(str(kind.pid))\n"
        "time.sleep(120)\n"
    )
    assert benchmark_code.run_pruefung(code, "import loesung\n", timeout_s=1.0) == (
        False,
        benchmark_code.GRUND_ZEITLIMIT,
    )
    pid = int(pid_datei.read_text(encoding="utf-8"))
    assert _beendet(pid), f"Kindprozess {pid} lebt nach dem Zeitlimit noch"


def test_modellcode_sieht_keine_schluessel_der_elternumgebung(monkeypatch):
    """Die Umgebung ist leer bis auf PATH: kein Schlüssel, kein HOME, kein PYTHONPATH.

    `LC_CTYPE` darf der Interpreter selbst setzen (PEP-538-Sprachumstellung), geerbt wird nichts.
    """
    monkeypatch.setenv("BENCH_TEST_GEHEIM", "geheimwert")
    pruefung = (
        "import os\n"
        "assert os.environ.get('PATH') == '/usr/bin:/bin'\n"
        "assert 'BENCH_TEST_GEHEIM' not in os.environ\n"
        "assert not any('geheimwert' in wert for wert in os.environ.values())\n"
        "assert set(os.environ) <= {'PATH', 'LC_CTYPE'}, sorted(os.environ)\n"
    )
    assert benchmark_code.run_pruefung("", pruefung) == (True, "")


@pytest.mark.parametrize("aufgabe_id", AUFGABEN_IDS)
def test_weitere_falsche_loesungen_fallen_durch(aufgabe_id):
    task = _aufgabe(aufgabe_id)
    for code in WEITERE_FALSCH[aufgabe_id]:
        assert benchmark_code.score_code(task, _antwort(code)) == {
            "bestanden": False,
            "grund": benchmark_code.GRUND_FEHLGESCHLAGEN,
        }, code


@pytest.mark.parametrize("aufgabe_id", AUFGABEN_IDS)
def test_referenz_ueberlebt_den_auszug_aus_einer_antwort(aufgabe_id):
    task = _aufgabe(aufgabe_id)
    code = benchmark_code.extract_code(_antwort(task["referenz"]))
    assert code is not None
    assert benchmark_code.run_pruefung(code, task["pruefung"]) == (True, "")


def test_netzsperre_ersetzt_auch_die_klasse_direkt():
    """`from socket import socket` bekommt die gesperrte Klasse, nicht das Original."""
    code = "from socket import socket\nsocket()\n"
    assert benchmark_code.run_pruefung(code, "import loesung\n") == (
        False,
        benchmark_code.GRUND_NETZ,
    )
