import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "autodeploy.py"
_spec = importlib.util.spec_from_file_location("autodeploy_install_fehler", _PATH)
ad = importlib.util.module_from_spec(_spec)
sys.modules["autodeploy_install_fehler"] = ad
_spec.loader.exec_module(ad)

ALT, NEU = "1111111", "2222222"
PIP = (
    "Collecting numpy>=2.5.4\n"
    "  ERROR: Ignored the following versions that require a different python version: 2.5.4 Requires-Python >=3.12\n"
    "ERROR: Could not find a version that satisfies the requirement numpy>=2.5.4 (from versions: 1.0, 2.4.1)\n"
    "ERROR: No matching distribution found for numpy>=2.5.4\n"
    "ERROR: No matching distribution found for numpy>=2.5.4\n"
    "ERROR: Could not install from https://user:pw@example.invalid/simple\n"
)


def _cp(cmd, rc=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, rc, out, err)


def test_install_fehler_nimmt_nur_pip_fehler():
    got = ad.install_fehler(PIP)
    assert got == [
        "ERROR: Ignored the following versions that require a different python version: 2.5.4 Requires-Python >=3.12",
        "ERROR: Could not find a version that satisfies the requirement numpy>=2.5.4 (from versions: 1.0, 2.4.1)",
        "ERROR: No matching distribution found for numpy>=2.5.4",
    ]
    assert ad.install_fehler(PIP, limit=1) == [got[0]]
    assert ad.install_fehler("") == [] and ad.install_fehler(PIP, limit=0) == []


def test_install_fehler_laesst_urls_und_at_weg_und_kuerzt():
    assert ad.install_fehler("ERROR: x https://a.invalid/simple\nERROR: token@host\n") == []
    lang = "ERROR: " + "y" * 300
    assert ad.install_fehler(lang) == [lang[:160]]


def test_rollback_meldung_nennt_die_pip_fehler(tmp_path):
    cmds, notes = [], []

    def run(cmd):
        c = list(cmd)
        cmds.append(c)
        if c[:3] == ["git", "rev-parse", "--short"]:
            return _cp(cmd, 0, (ALT if "HEAD" in c else NEU) + "\n")
        if c == ["installer"]:
            return _cp(cmd, 1, "Einrichtung …\n", PIP)
        return _cp(cmd, 0)

    (tmp_path / ".deploy-state.json").write_text(json.dumps({"last_good": ALT}), encoding="utf-8")
    res = ad.deploy_once(tmp_path, run, notes.append, is_busy=lambda: False,
                         test_cmd=["test-runner"], install_cmd=["installer"])
    assert res.status == "zurueckgerollt"
    assert res.detail == (
        "install.sh fehlgeschlagen (Exit 1): "
        "ERROR: Ignored the following versions that require a different python version: 2.5.4 Requires-Python >=3.12; "
        "ERROR: Could not find a version that satisfies the requirement numpy>=2.5.4 (from versions: 1.0, 2.4.1); "
        "ERROR: No matching distribution found for numpy>=2.5.4"
        f"; erneutes install.sh auf {ALT} ebenfalls (Exit 1)"
    )
    assert any(n.startswith("⚠ Stephan:") and "numpy>=2.5.4" in n for n in notes)
    assert "pw" not in " ".join(notes)


def test_ohne_pip_fehler_bleibt_die_meldung_wie_bisher(tmp_path):
    def run(cmd):
        c = list(cmd)
        if c[:3] == ["git", "rev-parse", "--short"]:
            return _cp(cmd, 0, (ALT if "HEAD" in c else NEU) + "\n")
        if c == ["installer"]:
            return _cp(cmd, 2, "irgendwas\n", "kaputt\n")
        return _cp(cmd, 0)

    (tmp_path / ".deploy-state.json").write_text(json.dumps({"last_good": ALT}), encoding="utf-8")
    res = ad.deploy_once(tmp_path, run, lambda m: None, is_busy=lambda: False,
                         test_cmd=["test-runner"], install_cmd=["installer"])
    assert res.detail == f"install.sh fehlgeschlagen (Exit 2); erneutes install.sh auf {ALT} ebenfalls (Exit 2)"
