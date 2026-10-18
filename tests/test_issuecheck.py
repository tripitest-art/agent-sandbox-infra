"""Tests für scripts/issuecheck.py (Planer-Preflight, ohne Netzwerk)."""
from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
TEMPLATE = ROOT / ".github" / "ISSUE_TEMPLATE" / "worker-task.yml"

# Modul per Pfad laden (scripts/ ist kein Paket)
_spec = importlib.util.spec_from_file_location("issuecheck", SCRIPTS_DIR / "issuecheck.py")
ic = importlib.util.module_from_spec(_spec)
sys.modules["issuecheck"] = ic
_spec.loader.exec_module(ic)

GOOD_BLOCK = "from sandbox.neu import unbekannte_funktion\n\n\ndef test_feature():\n    assert unbekannte_funktion() == 42\n"


def _issue(
    block: str = GOOD_BLOCK,
    merge: str = "automatisch nach Review",
    dateien: str = "- `sandbox/neu.py`\n- `tests/acceptance/test_issue_<N>.py`\n- `tests/test_neu.py`",
    fence: str = "python",
    extra_headings: dict[str, str] | None = None,
) -> str:
    """Erzeugt einen standardkonformen Issue-Text gemäß worker-task.yml."""
    bodies = {
        "Ziel": "Neues Feature in sandbox/neu.py bereitstellen.",
        "Betroffene Dateien": dateien,
        "Schnittstellen": "```python\ndef unbekannte_funktion() -> int:\n    pass\n```",
        "Nicht Teil dieser Aufgabe": "- kein Umbau bestehender Module",
        "Akzeptanztests": f"```{fence}\n{block}```",
        "Fertig, wenn": "- [ ] Akzeptanztests grün\n- [ ] Weitere Tests decken das Verhalten ab",
        "Merge-Modus": merge,
        "Kontext": "Planer: Antigravity / Claude.",
    }
    if extra_headings:
        bodies.update(extra_headings)

    return "\n\n".join(f"### {name}\n\n{bodies[name]}" for name in ic.REQUIRED_HEADINGS) + "\n"


def _template_labels() -> list[str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    return [m.group(1).strip() for m in re.finditer(r"^\s+label:\s*(.+)$", text, re.M)]


def _codes(findings: list[ic.Finding]) -> list[str]:
    return sorted(f.code for f in findings)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "sandbox").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "sandbox" / "vorhanden.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return r


# ---------------- Formular & Pflichtüberschriften ----------------
def test_formular_ueberschriften_entsprechen_den_pflichtueberschriften():
    assert _template_labels() == list(ic.REQUIRED_HEADINGS)


def test_standard_issue_besteht_check_static():
    findings = ic.check_static(_issue())
    assert findings == []


def test_fehlende_ueberschrift_wird_als_fehler_gemeldet():
    # Issue ohne "Schnittstellen"
    text = "\n\n".join(f"### {name}\n\nInhalt" for name in ic.REQUIRED_HEADINGS if name != "Schnittstellen")
    findings = ic.check_static(text)
    assert "ueberschrift-fehlt" in _codes(findings)


def test_falsche_reihenfolge_der_ueberschriften_erzeugt_warnung():
    # Reihenfolge vertauschen: Kontext vor Ziel
    shuffled = ["Kontext"] + [h for h in ic.REQUIRED_HEADINGS if h != "Kontext"]
    text = "\n\n".join(f"### {name}\n\nInhalt" for name in shuffled)
    findings = ic.check_static(text)
    assert "reihenfolge" in _codes(findings)


# ---------------- Betroffene Dateien & Merge-Modus ----------------
def test_betroffene_dateien_ohne_akzeptanztest_ist_fehler():
    bad_dateien = "- `sandbox/neu.py`\n- `tests/test_neu.py`"
    findings = ic.check_static(_issue(dateien=bad_dateien))
    assert "betroffene-dateien-test" in _codes(findings)


def test_ungueltiger_merge_modus_ist_fehler():
    findings = ic.check_static(_issue(merge="vielleicht"))
    assert "merge-modus" in _codes(findings)


def test_aufsichtsdatei_mit_automatisch_ist_fehler():
    # Berührt AGENTS.md, aber automatischer Merge gewählt
    bad_dateien = "- `AGENTS.md`\n- `tests/acceptance/test_issue_<N>.py`"
    findings = ic.check_static(_issue(dateien=bad_dateien, merge="automatisch nach Review"))
    assert "manual-paths-automatisch" in _codes(findings)


def test_aufsichtsdatei_mit_manuell_ist_gueltig():
    ok_dateien = "- `AGENTS.md`\n- `tests/acceptance/test_issue_<N>.py`"
    findings = ic.check_static(_issue(dateien=ok_dateien, merge="manuell durch Stephan (Betrieb, Server, Zugangsdaten)"))
    assert "manual-paths-automatisch" not in _codes(findings)


def test_wartet_auf_zeile_im_akzeptanztest_ist_fehler():
    # Versehentliche Blockade-Zeile (#46)
    bad_block = (
        "# Wartet auf: #42\n"
        "from sandbox.neu import f\n\n"
        "def test_f():\n"
        "    assert f() == 1\n"
    )
    findings = ic.check_static(_issue(block=bad_block))
    assert "wartet-auf-im-test" in _codes(findings)


# ---------------- Akzeptanztest-Code & AST ----------------
def test_leerer_block_ist_kein_block():
    assert _codes(ic.check_static(_issue(block=""))) == ["kein-block"]


def test_mehrere_codebloecke_sind_fehler():
    multi_block = "```python\ndef test_a(): pass\n```\n\n```python\ndef test_b(): pass\n```"
    text = _issue(block="dummy").replace("```python\ndummy```", multi_block)
    findings = ic.check_static(text)
    assert "mehrere-bloecke" in _codes(findings)


def test_syntaxfehler_im_code():
    bad_syntax = "def test_x(:\n    pass\n"
    findings = ic.check_static(_issue(block=bad_syntax))
    assert "syntaxfehler" in _codes(findings)


def test_keine_testfunktion():
    no_test = "def helper():\n    return 42\n"
    findings = ic.check_static(_issue(block=no_test))
    assert "kein-test" in _codes(findings)


def test_verbotene_importe():
    for mod in ("requests", "httpx", "socket", "http.client", "random"):
        block = f"import {mod}\n\ndef test_x():\n    assert True\n"
        findings = ic.check_static(_issue(block=block))
        assert "verbotener-import" in _codes(findings), f"Modul {mod} sollte verboten sein"


def test_skip_und_xfail_attributzugriffe():
    block = "import pytest\n\n@pytest.mark.skip(reason='spaeter')\ndef test_x():\n    assert True\n"
    findings = ic.check_static(_issue(block=block))
    assert "skip-xfail" in _codes(findings)


def test_uhrzeit_und_zufall_aufrufe():
    for call in ("time.sleep(1)", "time.time()", "datetime.datetime.now()", "datetime.datetime.utcnow()"):
        block = (
            "import time\n"
            "import datetime\n\n"
            f"def test_x():\n"
            f"    {call}\n"
            "    assert True\n"
        )
        findings = ic.check_static(_issue(block=block))
        assert "verbotener-aufruf" in _codes(findings), f"Aufruf {call} sollte verboten sein"


def test_root_pfad_verbot():
    block = (
        "def test_x():\n"
        "    path = '/root/worker.log'\n"
        "    assert path\n"
    )
    findings = ic.check_static(_issue(block=block))
    assert "root-pfad" in _codes(findings)


def test_string_literale_mit_skip_sind_erlaubt():
    # String-Literale mit Namen wie "pytest.skip" sind keine echten Skip-Marker
    block = (
        "def test_parser():\n"
        "    code = 'pytest.skip(\"grund\")'\n"
        "    assert 'skip' in code\n"
    )
    findings = ic.check_static(_issue(block=block))
    assert "skip-xfail" not in _codes(findings)


# ---------------- Testlauf (check_red) ----------------
def test_check_red_erfolgreich_bei_rotem_test(repo):
    # Test importiert fehlendes Modul -> vor Umsetzung rot (wie gewünscht)
    findings, out = ic.check_red(_issue(block=GOOD_BLOCK), repo=repo)
    assert findings == []


def test_check_red_meldet_fehler_wenn_schon_gruen(repo):
    green_block = (
        "from sandbox.vorhanden import f\n\n"
        "def test_vorhanden():\n"
        "    assert f() == 1\n"
    )
    findings, out = ic.check_red(_issue(block=green_block), repo=repo)
    assert "schon-gruen" in _codes(findings)


def test_check_red_meldet_teilweise_gruen(repo):
    mixed_block = (
        "from sandbox.vorhanden import f\n\n"
        "def test_ok():\n"
        "    assert f() == 1\n\n"
        "def test_fail():\n"
        "    assert f() == 999\n"
    )
    findings, out = ic.check_red(_issue(block=mixed_block), repo=repo)
    assert "teilweise-gruen" in _codes(findings)


# ---------------- CLI main ----------------
def test_main_mit_datei_erfolgreich(repo, tmp_path, capsys):
    p = tmp_path / "issue.md"
    p.write_text(_issue(), encoding="utf-8")
    ret = ic.main([str(p), "--repo", str(repo)])
    assert ret == 0
    assert "bereit für die Sandbox" in capsys.readouterr().out


def test_main_stdin(repo, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(_issue()))
    ret = ic.main(["-", "--repo", str(repo)])
    assert ret == 0
    assert "bereit für die Sandbox" in capsys.readouterr().out


def test_main_format_only_fuehrt_kein_pytest_aus(tmp_path, capsys):
    p = tmp_path / "issue.md"
    p.write_text(_issue(), encoding="utf-8")
    ret = ic.main([str(p), "--format-only"])
    assert ret == 0
    assert "bereit für die Sandbox" in capsys.readouterr().out


def test_main_exitcode_eins_bei_statischem_fehler(tmp_path, capsys):
    p = tmp_path / "bad.md"
    p.write_text(_issue(block="import requests\n\ndef test_x(): pass\n"), encoding="utf-8")
    ret = ic.main([str(p), "--format-only"])
    assert ret == 1
    assert "Nicht einstellen: 1 Fehler" in capsys.readouterr().out
