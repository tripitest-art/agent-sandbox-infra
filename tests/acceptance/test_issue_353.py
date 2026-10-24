import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _lade(name, datei):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ad = _lade("autodeploy_vorrang", "autodeploy.py")
w = _lade("worker_tick_vorrang", "worker_tick.py")


class Git:
    """Ersatz für `run`: antwortet nach Tabelle und merkt sich die Befehle."""

    def __init__(self, old="1111111", new="2222222", fetch_rc=0):
        self.old, self.new, self.fetch_rc = old, new, fetch_rc
        self.commands = []

    def __call__(self, cmd):
        c = list(cmd)
        self.commands.append(c)
        if c[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(c, self.fetch_rc, "", "")
        if c[:3] == ["git", "rev-parse", "--short"]:
            return subprocess.CompletedProcess(c, 0, (self.old if "HEAD" in c else self.new) + "\n", "")
        return subprocess.CompletedProcess(c, 0, "", "")


def _busy():
    return ad.DeployResult("uebersprungen", None, None, ad.BUSY_DETAIL)


def test_beschaeftigter_takt_mit_neuem_stand_legt_die_marke_an(tmp_path):
    marke = tmp_path / "pending"
    git = Git()
    assert ad.mark_pending(tmp_path, git, _busy(), pending_file=str(marke)) == "2222222"
    assert marke.read_text(encoding="utf-8").strip() == "2222222"
    assert ["git", "fetch", "--quiet", "origin", "main"] in git.commands


def test_beschaeftigter_takt_ohne_neuen_stand_entfernt_die_marke(tmp_path):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    assert ad.mark_pending(tmp_path, Git(old="3333333", new="3333333"), _busy(),
                           pending_file=str(marke)) is None
    assert not marke.exists()


def test_fehlerhafter_stand_bekommt_keine_marke(tmp_path):
    (tmp_path / ad.STATE_FILE).write_text(json.dumps({"bad": "2222222"}), encoding="utf-8")
    marke = tmp_path / "pending"
    assert ad.mark_pending(tmp_path, Git(), _busy(), pending_file=str(marke)) is None
    assert not marke.exists()


def test_fetch_fehler_laesst_die_marke_unveraendert(tmp_path):
    marke = tmp_path / "pending"
    marke.write_text("9999999\n", encoding="utf-8")
    assert ad.mark_pending(tmp_path, Git(fetch_rc=1), _busy(), pending_file=str(marke)) is None
    assert marke.read_text(encoding="utf-8").strip() == "9999999"


@pytest.mark.parametrize("status", ["ausgerollt", "unveraendert", "zurueckgerollt", "fehler"])
def test_lief_der_deploy_wird_die_marke_ohne_git_aufruf_entfernt(tmp_path, status):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    git = Git()
    res = ad.DeployResult(status, "1111111", "2222222", "egal")
    assert ad.mark_pending(tmp_path, git, res, pending_file=str(marke)) is None
    assert not marke.exists()
    assert git.commands == []


def test_vorrang_fehlende_marke_beim_entfernen_ist_kein_fehler(tmp_path):
    res = ad.DeployResult("unveraendert", "1", "1", "x")
    assert ad.mark_pending(tmp_path, Git(), res, pending_file=str(tmp_path / "nichtda")) is None


def test_deploy_steht_an_bei_junger_marke(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    jetzt = marke.stat().st_mtime + 60
    logs = []
    monkeypatch.setattr(w, "log", logs.append)
    monkeypatch.setattr(w, "save_state", lambda state: None)
    state = {}
    assert w.deploy_steht_an(state, path=str(marke), now=jetzt) is True
    assert any("2222222" in z and "keine neuen Issue-Aufträge" in z for z in logs)
    assert w.deploy_steht_an(state, path=str(marke), now=jetzt) is True
    assert len(logs) == 1, "die Meldung erscheint nur beim Wechsel (log_on_change)"


def test_alte_marke_sperrt_die_worker_nicht_und_meldet_laut(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    jetzt = marke.stat().st_mtime + w.DEPLOY_PENDING_MAX_AGE_S + 1
    logs = []
    monkeypatch.setattr(w, "log", logs.append)
    monkeypatch.setattr(w, "save_state", lambda state: None)
    assert w.deploy_steht_an({}, path=str(marke), now=jetzt) is False
    assert any(z.startswith("⚠ Stephan:") and "2222222" in z for z in logs)


def test_fehlende_marke_raeumt_den_zustand_und_sperrt_nicht(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "log", lambda m: None)
    gespeichert = []
    monkeypatch.setattr(w, "save_state", lambda state: gespeichert.append(dict(state)))
    state = {"_deploypending": "2222222"}
    assert w.deploy_steht_an(state, path=str(tmp_path / "nichtda"), now=1.0) is False
    assert "_deploypending" not in state and gespeichert


def test_find_job_nimmt_bei_ausstehendem_deploy_kein_issue_an(monkeypatch):
    job = ("issue", 501)
    monkeypatch.setattr(w, "IS_BILLIG_SLOT", False)
    monkeypatch.setattr(w, "project_done", lambda state: set())
    monkeypatch.setattr(w, "rework_job", lambda done, skip: None)
    monkeypatch.setattr(w, "text_fix_job", lambda skip: None)
    monkeypatch.setattr(w, "issue_job", lambda skip, state=None: job)
    monkeypatch.setattr(w, "main_is_red", lambda state: False)
    monkeypatch.setattr(w, "main_is_red_local", lambda state: False)
    monkeypatch.setattr(w, "deploy_steht_an", lambda state, path=None, now=None: True)
    assert w.find_job({}) is None
    monkeypatch.setattr(w, "deploy_steht_an", lambda state, path=None, now=None: False)
    assert w.find_job({}) == job


def test_find_job_laesst_nacharbeit_bei_ausstehendem_deploy_durch(monkeypatch):
    nacharbeit = ("rework", 7)
    monkeypatch.setattr(w, "IS_BILLIG_SLOT", False)
    monkeypatch.setattr(w, "project_done", lambda state: set())
    monkeypatch.setattr(w, "rework_job", lambda done, skip: nacharbeit)
    monkeypatch.setattr(w, "deploy_steht_an", lambda state, path=None, now=None: True)
    assert w.find_job({}) == nacharbeit
