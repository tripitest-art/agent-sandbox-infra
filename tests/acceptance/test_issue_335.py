import os
import signal
import subprocess
import tempfile

import pytest

from sandbox.core import benchmark_code

IDS = ["dauer_parsen", "rate_limiter", "wartet_auf"]

SIGNATUREN = {
    "dauer_parsen": ["def parse_dauer(text: str) -> int"],
    "rate_limiter": [
        "class RateLimiter",
        "def __init__(self, max_aufrufe: int, fenster_s: float, uhr: Callable[[], float])",
        "def erlaubt(self) -> bool",
    ],
    "wartet_auf": ["def parse_wartet_auf(text: str) -> list[int]"],
}

STUBS = {
    "dauer_parsen": "def parse_dauer(text):\n    pass\n",
    "rate_limiter": (
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        pass\n"
        "    def erlaubt(self):\n"
        "        pass\n"
    ),
    "wartet_auf": "def parse_wartet_auf(text):\n    pass\n",
}

FALSCH = {
    "dauer_parsen": [
        # überliest Fremdtext, statt ValueError auszulösen
        "import re\n"
        "def parse_dauer(text):\n"
        "    f = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}\n"
        "    return sum(int(n) * f[u] for n, u in re.findall(r'(\\d+)([dhms])', text))\n",
        # leere Zeichenkette ergibt 0 statt ValueError
        "import re\n"
        "def parse_dauer(text):\n"
        "    m = re.fullmatch(r'(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?', text)\n"
        "    if m is None:\n"
        "        raise ValueError(text)\n"
        "    d, h, mi, s = (int(x or 0) for x in m.groups())\n"
        "    return d * 86400 + h * 3600 + mi * 60 + s\n",
        # '$' lässt einen Zeilenumbruch am Ende durch
        "import re\n"
        "def parse_dauer(text):\n"
        "    m = re.match(r'^(?=.)(?:(\\d+)d)?(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?$', text)\n"
        "    if m is None:\n"
        "        raise ValueError(text)\n"
        "    d, h, mi, s = (int(x or 0) for x in m.groups())\n"
        "    return d * 86400 + h * 3600 + mi * 60 + s\n",
    ],
    "rate_limiter": [
        # zählt auch abgelehnte Aufrufe
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        if max_aufrufe < 1 or fenster_s <= 0:\n"
        "            raise ValueError('x')\n"
        "        self.m, self.f, self.u, self.t = max_aufrufe, fenster_s, uhr, []\n"
        "    def erlaubt(self):\n"
        "        jetzt = self.u()\n"
        "        self.t = [x for x in self.t if x > jetzt - self.f]\n"
        "        self.t.append(jetzt)\n"
        "        return len(self.t) <= self.m\n",
        # Fenster auch links geschlossen
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        if max_aufrufe < 1 or fenster_s <= 0:\n"
        "            raise ValueError('x')\n"
        "        self.m, self.f, self.u, self.t = max_aufrufe, fenster_s, uhr, []\n"
        "    def erlaubt(self):\n"
        "        jetzt = self.u()\n"
        "        self.t = [x for x in self.t if x >= jetzt - self.f]\n"
        "        if len(self.t) < self.m:\n"
        "            self.t.append(jetzt)\n"
        "            return True\n"
        "        return False\n",
        # fester Zeitblock statt gleitendem Fenster
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        if max_aufrufe < 1 or fenster_s <= 0:\n"
        "            raise ValueError('x')\n"
        "        self.m, self.f, self.u = max_aufrufe, fenster_s, uhr\n"
        "        self.start, self.n = None, 0\n"
        "    def erlaubt(self):\n"
        "        jetzt = self.u()\n"
        "        if self.start is None or jetzt - self.start >= self.f:\n"
        "            self.start, self.n = jetzt, 0\n"
        "        if self.n < self.m:\n"
        "            self.n += 1\n"
        "            return True\n"
        "        return False\n",
        # erlaubt alles
        "class RateLimiter:\n"
        "    def __init__(self, max_aufrufe, fenster_s, uhr):\n"
        "        pass\n"
        "    def erlaubt(self):\n"
        "        return True\n",
    ],
    "wartet_auf": [
        # liest den ganzen Text statt nur die Zeilen mit 'Wartet auf:'
        "import re\n"
        "def parse_wartet_auf(text):\n"
        "    return sorted({int(n) for n in re.findall(r'#(\\d+)', text)})\n",
        # beachtet die Codeblöcke nicht
        "import re\n"
        "def parse_wartet_auf(text):\n"
        "    out = set()\n"
        "    for z in text.splitlines():\n"
        "        if z.strip().lower().startswith('wartet auf:'):\n"
        "            out.update(int(n) for n in re.findall(r'#(\\d+)', z))\n"
        "    return sorted(out)\n",
        # 'Wartet auf:' auch mitten in der Zeile
        "import re\n"
        "def parse_wartet_auf(text):\n"
        "    out, im_block = set(), False\n"
        "    for z in text.splitlines():\n"
        "        if z.lstrip().startswith('```'):\n"
        "            im_block = not im_block\n"
        "            continue\n"
        "        if not im_block and 'wartet auf:' in z.lower():\n"
        "            out.update(int(n) for n in re.findall(r'#(\\d+)', z))\n"
        "    return sorted(out)\n",
        # sortiert als Text ('10' vor '9')
        "import re\n"
        "def parse_wartet_auf(text):\n"
        "    out, im_block = set(), False\n"
        "    for z in text.splitlines():\n"
        "        if z.lstrip().startswith('```'):\n"
        "            im_block = not im_block\n"
        "            continue\n"
        "        if not im_block and z.lstrip().lower().startswith('wartet auf:'):\n"
        "            out.update(re.findall(r'#(\\d+)', z))\n"
        "    return [int(n) for n in sorted(out)]\n",
    ],
}


def _gezaeunt(code):
    return "Hier ist meine Lösung:\n\n```python\n" + code + "\n```\n\nViel Spaß!"


def _aufgabe(aufgabe_id):
    return {t["id"]: t for t in benchmark_code.CODE_TASKS}[aufgabe_id]


def _spion_popen(monkeypatch):
    echt = subprocess.Popen
    gestartet = []

    class Spion(echt):
        def __init__(self, *a, **k):
            self.beobachtet = (a, k)
            super().__init__(*a, **k)
            gestartet.append(self)

    monkeypatch.setattr(subprocess, "Popen", Spion)
    return gestartet


# --- extract_code ---------------------------------------------------------------------------

def test_extract_code_nimmt_den_ersten_python_block_mit_def():
    antwort = "Text\n```python\ndef f():\n    return 1\n```\nund\n```python\ndef g():\n    pass\n```"
    assert benchmark_code.extract_code(antwort).strip() == "def f():\n    return 1"


def test_extract_code_ueberspringt_beispielbloecke_ohne_def_oder_class():
    antwort = "```python\nprint(1)\n```\ntext\n```python\nclass A:\n    x = 1\n```"
    assert benchmark_code.extract_code(antwort).strip() == "class A:\n    x = 1"


def test_extract_code_akzeptiert_unmarkierten_zaun():
    assert benchmark_code.extract_code("```\ndef f():\n    return 2\n```").strip() == "def f():\n    return 2"


def test_extract_code_ignoriert_fremde_sprachen():
    assert benchmark_code.extract_code("```bash\ndef f(): echo\n```") is None
    assert benchmark_code.extract_code("```json\n{\"class \": 1}\n```") is None


def test_extract_code_ohne_zaun_oder_ohne_def_ist_none():
    assert benchmark_code.extract_code("def f():\n    return 1") is None
    assert benchmark_code.extract_code("") is None
    assert benchmark_code.extract_code("```python\nx = 1\n```") is None
    assert benchmark_code.extract_code(None) is None


def test_extract_code_verkraftet_backticks_mitten_im_code():
    antwort = "```python\ndef f(text):\n    return text.startswith(\"```\")\n```\nEnde"
    assert benchmark_code.extract_code(antwort).strip() == 'def f(text):\n    return text.startswith("```")'


def test_extract_code_behaelt_die_einrueckung():
    code = "class A:\n    def f(self):\n        return 1\n"
    assert benchmark_code.extract_code("```python\n" + code + "```").strip() == code.strip()


# --- run_pruefung: Ergebnis ------------------------------------------------------------------

def test_run_pruefung_besteht():
    assert benchmark_code.run_pruefung(
        "def f():\n    return 1\n", "from loesung import f\nassert f() == 1\n"
    ) == (True, "")


def test_run_pruefung_fehlgeschlagene_zusicherung():
    assert benchmark_code.run_pruefung(
        "def f():\n    return 2\n", "from loesung import f\nassert f() == 1\n"
    ) == (False, "Prüfung fehlgeschlagen")


def test_run_pruefung_syntaxfehler_im_modellcode():
    assert benchmark_code.run_pruefung("def f(:\n", "import loesung\n") == (False, "Prüfung fehlgeschlagen")


def test_exit_null_im_modellcode_taeuscht_kein_bestehen_vor():
    for code in ("import sys\nsys.exit(0)\n", "import os\nos._exit(0)\n"):
        assert benchmark_code.run_pruefung(code, "import loesung\nassert False\n") == (
            False, "Prüfung fehlgeschlagen")


def test_ausgabe_des_modellcodes_steht_nie_im_grund():
    code = "import sys\nprint('GEHEIMMARKER_4711')\nprint('GEHEIMMARKER_4711', file=sys.stderr)\nraise ValueError('GEHEIMMARKER_4711')\n"
    ok, grund = benchmark_code.run_pruefung(code, "import loesung\n")
    assert (ok, grund) == (False, "Prüfung fehlgeschlagen")
    antwort = benchmark_code.score_code({"pruefung": "import loesung\n"}, _gezaeunt("def f():\n    pass\n" + code))
    assert "GEHEIMMARKER" not in str(antwort)


def test_run_pruefung_wirft_nie():
    for code in ("\x00", "x = '\ud800'\n", "", "\n\n\n"):
        ergebnis = benchmark_code.run_pruefung(code, "import loesung\n")
        assert isinstance(ergebnis, tuple) and isinstance(ergebnis[0], bool) and isinstance(ergebnis[1], str)
    assert benchmark_code.run_pruefung("\x00", "import loesung\n") == (False, "Prüfung fehlgeschlagen")
    assert benchmark_code.run_pruefung("", "\x00") == (False, "Prüfung fehlgeschlagen")
    assert benchmark_code.run_pruefung("", "import loesung\n") == (True, "")


# --- run_pruefung: Zeitlimit ----------------------------------------------------------------

def test_zeitlimit_bei_endlosschleife():
    code = "while True:\n    pass\n"
    assert benchmark_code.run_pruefung(code, "import loesung\n", timeout_s=1.0) == (False, "Zeitlimit")


def test_zeitlimit_nach_wanduhr_auch_ohne_cpu_last():
    code = "import time\ntime.sleep(60)\n"
    assert benchmark_code.run_pruefung(code, "import loesung\n", timeout_s=1.0) == (False, "Zeitlimit")


def test_zeitlimit_toetet_die_prozessgruppe(monkeypatch):
    gestartet = _spion_popen(monkeypatch)
    kills = []
    echt_killpg = os.killpg

    def killpg(pgid, sig):
        kills.append((pgid, sig))
        return echt_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", killpg)
    benchmark_code.run_pruefung("import time\ntime.sleep(60)\n", "import loesung\n", timeout_s=1.0)
    assert len(gestartet) == 1
    assert (gestartet[0].pid, signal.SIGKILL) in kills


# --- run_pruefung: Umgebung, Verzeichnis, Aufruf ---------------------------------------------

def test_umgebung_enthaelt_keine_schluessel_der_elternumgebung(monkeypatch):
    monkeypatch.setenv("BENCH_GEHEIM_SCHLUESSEL", "geheimwert")
    monkeypatch.setenv("LITELLM_API_KEY", "geheimwert")
    pruefung = (
        "import os\n"
        "assert 'BENCH_GEHEIM_SCHLUESSEL' not in os.environ\n"
        "assert 'LITELLM_API_KEY' not in os.environ\n"
        "assert os.environ.get('PATH') == '/usr/bin:/bin'\n"
        "assert not any('geheimwert' in v for v in os.environ.values())\n"
    )
    assert benchmark_code.run_pruefung("", pruefung) == (True, "")


def test_aufruf_des_unterprozesses(monkeypatch, tmp_path):
    gestartet = _spion_popen(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert benchmark_code.run_pruefung("", "import loesung\n") == (True, "")
    assert len(gestartet) == 1
    a, k = gestartet[0].beobachtet
    argv = a[0] if a else k["args"]
    assert argv[1:] == ["-I", "pruefung.py"]
    assert k["env"] == {"PATH": "/usr/bin:/bin"}
    assert k["stdin"] == subprocess.DEVNULL
    assert k["start_new_session"] is True
    assert os.path.abspath(k["cwd"]) != str(tmp_path)
    assert not os.path.exists(k["cwd"])


def test_arbeitsverzeichnis_ist_fremd_und_danach_restlos_weg(tmp_path, monkeypatch):
    arbeit = tmp_path / "arbeit"
    temp = tmp_path / "temp"
    arbeit.mkdir()
    temp.mkdir()
    monkeypatch.chdir(arbeit)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    code = "open('spur.txt', 'w').write('x')\n"
    pruefung = (
        "import os\n"
        "import loesung\n"
        "assert os.path.exists('spur.txt')\n"
        "assert os.path.exists('loesung.py') and os.path.exists('pruefung.py')\n"
        f"assert os.getcwd() != {str(arbeit)!r}\n"
    )
    assert benchmark_code.run_pruefung(code, pruefung) == (True, "")
    assert list(arbeit.iterdir()) == []
    assert list(temp.iterdir()) == []


def test_verzeichnis_ist_auch_nach_zeitlimit_weg(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    benchmark_code.run_pruefung("import time\ntime.sleep(60)\n", "import loesung\n", timeout_s=1.0)
    assert list(tmp_path.iterdir()) == []


# --- run_pruefung: Netz und Ressourcen -------------------------------------------------------

@pytest.mark.parametrize("code", [
    "import socket\nsocket.socket()\n",
    "import socket\nsocket.create_connection(('127.0.0.1', 9))\n",
    "import socket\nsocket.getaddrinfo('example.org', 80)\n",
])
def test_netzzugriff_ist_verboten(code):
    assert benchmark_code.run_pruefung(code, "import loesung\n") == (False, "Netzzugriff verboten")


def test_ressourcengrenzen_gelten_im_unterprozess():
    pruefung = (
        "import resource\n"
        "assert resource.getrlimit(resource.RLIMIT_AS) == (512 * 2**20, 512 * 2**20)\n"
        "assert resource.getrlimit(resource.RLIMIT_FSIZE) == (2**20, 2**20)\n"
        "assert resource.getrlimit(resource.RLIMIT_CPU) == (%d, %d)\n"
    )
    assert benchmark_code.run_pruefung("", pruefung % (11, 11), timeout_s=10.0) == (True, "")
    assert benchmark_code.run_pruefung("", pruefung % (4, 4), timeout_s=2.5) == (True, "")


def test_speicher_und_dateigroesse_sind_begrenzt():
    viel_speicher = "buf = bytearray(1024 * 2**20)\n"
    grosse_datei = "with open('gross.bin', 'wb') as f:\n    f.write(b'x' * (4 * 2**20))\n"
    assert benchmark_code.run_pruefung(viel_speicher, "import loesung\n") == (False, "Prüfung fehlgeschlagen")
    assert benchmark_code.run_pruefung(grosse_datei, "import loesung\n") == (False, "Prüfung fehlgeschlagen")


# --- Katalog -----------------------------------------------------------------------------------

def test_katalog_hat_genau_die_drei_aufgaben_mit_allen_schluesseln():
    assert [t["id"] for t in benchmark_code.CODE_TASKS] == IDS
    for t in benchmark_code.CODE_TASKS:
        assert set(t) == {"id", "titel", "prompt", "pruefung", "referenz"}
        assert all(isinstance(v, str) and v.strip() for v in t.values())


@pytest.mark.parametrize("aufgabe_id", IDS)
def test_prompt_nennt_die_signatur_und_verraet_nichts(aufgabe_id):
    t = _aufgabe(aufgabe_id)
    prompt = benchmark_code.build_code_prompt(t)
    assert t["prompt"] in prompt
    assert "```python" in prompt
    for teil in SIGNATUREN[aufgabe_id]:
        assert teil in prompt
    assert t["referenz"].strip() not in prompt
    assert t["pruefung"].strip() not in prompt


@pytest.mark.parametrize("aufgabe_id", IDS)
def test_referenz_besteht_ihre_pruefung(aufgabe_id):
    t = _aufgabe(aufgabe_id)
    assert benchmark_code.run_pruefung(t["referenz"], t["pruefung"]) == (True, "")
    assert benchmark_code.score_code(t, _gezaeunt(t["referenz"])) == {"bestanden": True, "grund": None}


@pytest.mark.parametrize("aufgabe_id", IDS)
def test_stub_und_falsche_ansaetze_fallen_durch(aufgabe_id):
    t = _aufgabe(aufgabe_id)
    for code in [STUBS[aufgabe_id]] + FALSCH[aufgabe_id]:
        assert benchmark_code.score_code(t, _gezaeunt(code)) == {
            "bestanden": False, "grund": "Prüfung fehlgeschlagen"}, code


# --- score_code --------------------------------------------------------------------------------

def test_score_code_ohne_code():
    t = _aufgabe("dauer_parsen")
    erwartet = {"bestanden": False, "grund": "kein Code"}
    assert benchmark_code.score_code(t, "Das kann ich leider nicht.") == erwartet
    assert benchmark_code.score_code(t, "") == erwartet
    assert benchmark_code.score_code(t, "def parse_dauer(text):\n    return 1") == erwartet


def test_score_code_reicht_zeitlimit_und_netz_durch():
    t = {"pruefung": "import loesung\n"}
    zeit = _gezaeunt("import time\ntime.sleep(60)\ndef f():\n    pass\n")
    netz = _gezaeunt("import socket\nsocket.socket()\ndef f():\n    pass\n")
    assert benchmark_code.score_code(t, zeit, timeout_s=1.0) == {"bestanden": False, "grund": "Zeitlimit"}
    assert benchmark_code.score_code(t, netz) == {"bestanden": False, "grund": "Netzzugriff verboten"}
