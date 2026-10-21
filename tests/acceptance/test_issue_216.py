"""Akzeptanztest: Der Pre-Push-Hook startet den Testbefehl in sauberer, CI-gleicher Umgebung."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_hook_sauber", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_hook_sauber"] = w
_spec.loader.exec_module(w)


def hook_ausfuehren(tmp_path, testbefehl, **zusatz_env):
    """Schreibt den Hook mit `testbefehl` und führt ihn mit einer verschmutzten Umgebung aus."""
    w.install_pre_push_hook(str(tmp_path / "hooks"), testbefehl)
    env = dict(os.environ, GIT_DIR="/irgendwo/.git", LITELLM_API_KEY="geheim",
               AGENT_SETTINGS="/etc/agent-settings.json", MEINE_VARIABLE="1", **zusatz_env)
    return subprocess.run(["sh", str(tmp_path / "hooks" / "pre-push")], env=env,
                          capture_output=True, text=True)


def umgebung_im_testbefehl(tmp_path):
    ausgabe = tmp_path / "env.txt"
    res = hook_ausfuehren(tmp_path, f"env > {ausgabe.as_posix()}")
    assert res.returncode == 0, res.stderr
    werte = {}
    for zeile in ausgabe.read_text(encoding="utf-8").splitlines():
        name, _, wert = zeile.partition("=")
        werte[name] = wert
    return werte


def test_testbefehl_sieht_keine_fremden_variablen(tmp_path):
    if os.name == "nt":
        return   # der Hook ist ein sh-Skript; getestet wird auf dem Server und in CI
    werte = umgebung_im_testbefehl(tmp_path)
    for name in ("MEINE_VARIABLE", "LITELLM_API_KEY", "GIT_DIR"):
        assert name not in werte, name


def test_agent_dateien_zeigen_ins_leere_wie_in_der_ci(tmp_path):
    if os.name == "nt":
        return
    werte = umgebung_im_testbefehl(tmp_path)
    assert werte["AGENT_PROJECTS"] == "/nonexistent/agent-projects.json"
    assert werte["AGENT_SETTINGS"] == "/nonexistent/agent-settings.json"


def test_path_und_home_bleiben_erhalten(tmp_path):
    if os.name == "nt":
        return
    ausgabe = tmp_path / "env.txt"
    res = hook_ausfuehren(tmp_path, f"env > {ausgabe.as_posix()}",
                          PATH="/sentinel-bin" + os.pathsep + os.environ["PATH"], HOME="/sentinel-home")
    assert res.returncode == 0, res.stderr
    werte = dict(z.partition("=")[::2] for z in ausgabe.read_text(encoding="utf-8").splitlines())
    assert "sentinel-bin" in werte["PATH"]
    assert "sentinel-home" in werte["HOME"]


def test_roter_testbefehl_blockiert_weiter(tmp_path):
    if os.name == "nt":
        return
    res = hook_ausfuehren(tmp_path, "false")
    assert res.returncode == 1
    assert "Push abgelehnt" in res.stderr


def test_gruener_testbefehl_laesst_den_push_durch(tmp_path):
    if os.name == "nt":
        return
    assert hook_ausfuehren(tmp_path, "true").returncode == 0


def test_testbefehl_mit_anfuehrungszeichen_und_semikolon_bleibt_ganz(tmp_path):
    if os.name == "nt":
        return
    ausgabe = tmp_path / "ausgabe.txt"
    res = hook_ausfuehren(tmp_path, f'printf %s "a b" > {ausgabe.as_posix()}; printf %s \'c\' >> {ausgabe.as_posix()}')
    assert res.returncode == 0, res.stderr
    assert ausgabe.read_text(encoding="utf-8") == "a bc"


def test_hooktext_behaelt_unset_und_hat_kein_no_verify():
    h = w.pre_push_hook("python -m pytest -q")
    assert "unset" in h and "GIT_DIR" in h
    assert h.index("unset") < h.index("python -m pytest -q")
    assert "exit 1" in h
    assert "--no-verify" not in h
