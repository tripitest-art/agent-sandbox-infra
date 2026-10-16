"""Unit-Tests für sandbox/autodeploy.py (Issue #15): ohne git, Netzwerk, Server und Uhr."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"
spec = importlib.util.spec_from_file_location("autodeploy", SANDBOX / "autodeploy.py")
ad = importlib.util.module_from_spec(spec)
sys.modules["autodeploy"] = ad
spec.loader.exec_module(ad)


def cp(cmd, rc=0, out=""):
    return subprocess.CompletedProcess(cmd, rc, out, "")


class Git:
    """Ersatz für `run`: merkt sich alle Befehle, antwortet nach Tabelle."""

    def __init__(self, old="1111111", new="2222222", dirty="", fails=()):
        self.old, self.new, self.dirty, self.fails = old, new, dirty, list(fails)
        self.commands: list[list[str]] = []

    def __call__(self, cmd):
        c = list(cmd)
        self.commands.append(c)
        for prefix in self.fails:
            if c[:len(prefix)] == list(prefix):
                return cp(cmd, 1)
        if c[:2] == ["git", "status"]:
            return cp(cmd, out=self.dirty)
        if c[:3] == ["git", "rev-parse", "--short"]:
            return cp(cmd, out=(self.old if "HEAD" in c else self.new) + "\n")
        return cp(cmd)

    def count(self, *prefix):
        return sum(1 for c in self.commands if c[:len(prefix)] == list(prefix))


def deploy(tmp_path, git, **kw):
    notes: list[str] = []
    res = ad.deploy_once(tmp_path, git, notes.append, is_busy=kw.pop("is_busy", lambda: False),
                         test_cmd=["tests"], install_cmd=["install"], **kw)
    return res, notes


# ---- State ----

def test_state_roundtrip_und_fehlerfaelle(tmp_path):
    assert ad.load_state(tmp_path) == {}
    ad.save_state(tmp_path, {"last_good": "abc", "bad": None})
    assert ad.load_state(tmp_path) == {"last_good": "abc", "bad": None}
    assert not (tmp_path / ".deploy-state.json.tmp").exists()
    (tmp_path / ".deploy-state.json").write_text("{kaputt", encoding="utf-8")
    assert ad.load_state(tmp_path) == {}
    (tmp_path / ".deploy-state.json").write_text("[1]", encoding="utf-8")
    assert ad.load_state(tmp_path) == {}


# ---- is_worker_or_review_active ----

def slot(dir_, n, status):
    (dir_ / f"slot-{n}.json").write_text(json.dumps({"slot": n, "status": status}), encoding="utf-8")


def test_aktiv_nur_bei_running_slot(tmp_path):
    slots, locks = tmp_path / "slots", tmp_path / "locks"
    slots.mkdir()
    locks.mkdir()
    args = dict(slot_dir=str(slots), lock_dir=str(locks))
    assert ad.is_worker_or_review_active(**args) is False
    slot(slots, 1, "idle")
    assert ad.is_worker_or_review_active(**args) is False
    slot(slots, 2, "running")
    assert ad.is_worker_or_review_active(**args) is True


def test_kaputte_slotdatei_zaehlt_nicht_als_aktiv(tmp_path):
    (tmp_path / "slot-1.json").write_text("{halb", encoding="utf-8")
    assert ad.is_worker_or_review_active(str(tmp_path), str(tmp_path)) is False


def test_aktiv_bei_gehaltenem_takt_lock(tmp_path, monkeypatch):
    for name in ("qwen-worker-w1.lock", "qwen-review.lock"):
        (tmp_path / name).write_text("", encoding="utf-8")
    held = {str(tmp_path / "qwen-review.lock")}
    monkeypatch.setattr(ad, "_lock_held", lambda p: p in held)
    assert ad.is_worker_or_review_active(str(tmp_path), str(tmp_path)) is True
    held.clear()
    held.add(str(tmp_path / "qwen-worker-w1.lock"))
    assert ad.is_worker_or_review_active(str(tmp_path), str(tmp_path)) is True
    held.clear()
    assert ad.is_worker_or_review_active(str(tmp_path), str(tmp_path)) is False


def test_lock_datei_ohne_halter_ist_nicht_aktiv(tmp_path):
    lock = tmp_path / "qwen-review.lock"
    lock.write_text("", encoding="utf-8")
    assert ad._lock_held(str(lock)) is False
    assert ad._lock_held(str(tmp_path / "gibt-es-nicht.lock")) is False


# ---- deploy_once ----

def test_busy_fasst_nichts_an(tmp_path):
    git = Git()
    res, notes = deploy(tmp_path, git, is_busy=lambda: True)
    assert res.status == "uebersprungen" and "aktiv" in res.detail
    assert git.commands == [] and notes == []


def test_dirty_meldet_nur_einmal_pro_ziel_commit(tmp_path):
    git = Git(dirty=" M sandbox/install.sh\n")
    _, notes1 = deploy(tmp_path, git)
    res, notes2 = deploy(tmp_path, git)
    assert len(notes1) == 1 and "Lokale Änderungen" in notes1[0] and "⚠ Stephan:" in notes1[0]
    assert notes2 == [] and res.status == "uebersprungen"
    assert git.count("git", "pull") == 0
    git.new = "4444444"   # neuer Ziel-Commit: wieder eine Meldung
    _, notes3 = deploy(tmp_path, git)
    assert len(notes3) == 1


def test_status_fragt_nur_verfolgte_dateien(tmp_path):
    git = Git()
    deploy(tmp_path, git)
    assert "--untracked-files=no" in git.commands[0]


def test_fetch_fehler_meldet_einmal(tmp_path):
    git = Git(fails=[("git", "fetch")])
    res, notes = deploy(tmp_path, git)
    assert res.status == "fehler" and len(notes) == 1 and "⚠ Stephan:" in notes[0]
    _, notes2 = deploy(tmp_path, git)
    assert notes2 == []          # gleicher Fehler: keine zweite Meldung


def test_unveraendert_ruft_weder_pull_noch_tests(tmp_path):
    git = Git(old="aaaaaaa", new="aaaaaaa")
    res, notes = deploy(tmp_path, git)
    assert res.status == "unveraendert" and notes == []
    assert git.count("git", "pull") == 0 and ["tests"] not in git.commands


def test_schlechter_stand_wird_nicht_wiederholt(tmp_path):
    ad.save_state(tmp_path, {"last_good": "1111111", "bad": "2222222"})
    git = Git()
    res, notes = deploy(tmp_path, git)
    assert res.status == "uebersprungen" and "fehlerhaft" in res.detail
    assert git.count("git", "pull") == 0 and notes == []


def test_erfolg_reihenfolge_und_state(tmp_path):
    ad.save_state(tmp_path, {"bad": "0000000", "last_error": "alt"})
    git = Git()
    res, notes = deploy(tmp_path, git)
    assert res.status == "ausgerollt" and (res.old, res.new) == ("1111111", "2222222")
    order = [c for c in git.commands if c[0] != "git" or c[1] in ("pull", "reset")]
    assert order == [["git", "pull", "--ff-only", "origin", "main"], ["tests"], ["install"]]
    state = ad.load_state(tmp_path)
    assert state["last_good"] == "2222222" and state["bad"] is None and state["last_error"] is None
    assert notes == ["Auto-Deploy: 1111111 -> 2222222 ausgerollt"]


def test_pull_fehler_rollt_nicht_zurueck_und_testet_nicht(tmp_path):
    git = Git(fails=[("git", "pull")])
    res, notes = deploy(tmp_path, git)
    assert res.status == "fehler" and "⚠ Stephan:" in notes[0]
    assert git.count("git", "reset") == 0 and ["tests"] not in git.commands


def test_tests_rot_setzt_zurueck_und_installiert_nicht(tmp_path):
    git = Git(fails=[("tests",)])
    res, notes = deploy(tmp_path, git)
    assert res.status == "zurueckgerollt"
    assert ["git", "reset", "--hard", "1111111"] in git.commands
    assert ["install"] not in git.commands
    assert ad.load_state(tmp_path)["bad"] == "2222222"
    assert len(notes) == 1 and notes[0].startswith("⚠ Stephan:")


def test_install_fehler_setzt_zurueck_und_installiert_alten_stand(tmp_path):
    ad.save_state(tmp_path, {"last_good": "1111111"})
    calls = {"n": 0}

    class Inst(Git):
        def __call__(self, cmd):
            res = super().__call__(cmd)
            if list(cmd) == ["install"]:
                calls["n"] += 1
                return cp(cmd, 1 if calls["n"] == 1 else 0)   # erster Lauf rot, Rollback grün
            return res

    git = Inst()
    res, notes = deploy(tmp_path, git)
    assert res.status == "zurueckgerollt" and "install.sh fehlgeschlagen" in res.detail
    cmds = git.commands
    reset_at = cmds.index(["git", "reset", "--hard", "1111111"])
    assert cmds.count(["install"]) == 2 and cmds.index(["install"], reset_at) > reset_at
    state = ad.load_state(tmp_path)
    assert state["bad"] == "2222222" and state["last_good"] == "1111111"
    assert "⚠ Stephan:" in notes[0] and "ebenfalls" not in notes[0]


def test_rollback_install_scheitert_ebenfalls_steht_in_der_meldung(tmp_path):
    git = Git(fails=[("install",)])
    res, notes = deploy(tmp_path, git)
    assert res.status == "zurueckgerollt" and "ebenfalls" in res.detail
    assert "ebenfalls" in notes[0]


def test_meldungen_enthalten_keine_rohe_ausgabe(tmp_path):
    class Laut(Git):
        def __call__(self, cmd):
            if list(cmd) == ["tests"]:
                return subprocess.CompletedProcess(cmd, 1, "GEHEIM-STDOUT", "GEHEIM-STDERR")
            return super().__call__(cmd)

    res, notes = deploy(tmp_path, Laut())
    assert "GEHEIM" not in res.detail and all("GEHEIM" not in n for n in notes)
