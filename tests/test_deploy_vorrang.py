"""Unit-Tests für den Vorrang des Auto-Deploys (#353): ohne git, Netzwerk und Uhr."""
import importlib.util
import subprocess
import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[1] / "sandbox"


def _lade(name, datei):
    # Andere Tests laden dieselben Module unter diesem Namen; ein zweiter Import wäre unnötig.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SANDBOX / datei)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ad = _lade("autodeploy", "autodeploy.py")
w = _lade("worker_tick", "worker_tick.py")


class Git:
    """Ersatz für `run`: antwortet nach Tabelle und merkt sich die Befehle."""

    def __init__(self, old="1111111", new="2222222", fetch_rc=0, rev_rc=0):
        self.old, self.new, self.fetch_rc, self.rev_rc = old, new, fetch_rc, rev_rc
        self.commands = []

    def __call__(self, cmd):
        c = list(cmd)
        self.commands.append(c)
        if c[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(c, self.fetch_rc, "", "")
        if c[:3] == ["git", "rev-parse", "--short"]:
            if self.rev_rc != 0:
                return subprocess.CompletedProcess(c, self.rev_rc, "", "")
            return subprocess.CompletedProcess(c, 0, (self.old if "HEAD" in c else self.new) + "\n", "")
        return subprocess.CompletedProcess(c, 0, "", "")


def _busy():
    return ad.DeployResult("uebersprungen", None, None, ad.BUSY_DETAIL)


# ---- mark_pending ----

def test_marke_wechselt_atomar_auf_den_neuen_stand(tmp_path):
    marke = tmp_path / "pending"
    marke.write_text("1111111\n", encoding="utf-8")
    git = Git()
    assert ad.mark_pending(tmp_path, git, _busy(), pending_file=str(marke)) == "2222222"
    assert marke.read_text(encoding="utf-8") == "2222222\n"
    assert not (tmp_path / "pending.tmp").exists()


def test_fetch_fehler_ohne_marke_legt_keine_an(tmp_path):
    marke = tmp_path / "pending"
    git = Git(fetch_rc=1)
    assert ad.mark_pending(tmp_path, git, _busy(), pending_file=str(marke)) is None
    assert not marke.exists()
    assert git.commands == [["git", "fetch", "--quiet", "origin", "main"]]


def test_nicht_ermittelbarer_commit_legt_keine_marke_an(tmp_path):
    marke = tmp_path / "pending"
    assert ad.mark_pending(tmp_path, Git(rev_rc=1), _busy(), pending_file=str(marke)) is None
    assert not marke.exists()


def test_schreibfehler_ergibt_none(tmp_path):
    # Das Verzeichnis fehlt: der Deploy-Lauf darf daran nicht scheitern.
    marke = tmp_path / "fehlt" / "pending"
    assert ad.mark_pending(tmp_path, Git(), _busy(), pending_file=str(marke)) is None


def test_pending_file_none_liest_das_modulattribut(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    monkeypatch.setattr(ad, "PENDING_FILE", str(marke))
    assert ad.mark_pending(tmp_path, Git(), _busy()) == "2222222"
    assert marke.read_text(encoding="utf-8").strip() == "2222222"


def test_main_legt_die_marke_nach_dem_deploy(tmp_path, monkeypatch):
    res = ad.DeployResult("uebersprungen", None, None, ad.BUSY_DETAIL)
    gesehen = []
    monkeypatch.setattr(ad, "choose_test_cmd", lambda python, repo, run: (("x",), True))
    monkeypatch.setattr(ad, "deploy_once", lambda repo, run, log, **kw: res)
    monkeypatch.setattr(ad, "mark_pending",
                        lambda repo, run, result: gesehen.append(result) or None)
    assert ad.main(run=lambda cmd: subprocess.CompletedProcess(list(cmd), 0, "", ""),
                   log=lambda msg: None) == 0
    assert gesehen == [res]


# ---- deploy_steht_an ----

def test_junge_marke_liest_den_modulpfad(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    monkeypatch.setattr(w, "DEPLOY_PENDING_FILE", str(marke))
    monkeypatch.setattr(w, "log", lambda msg: None)
    monkeypatch.setattr(w, "save_state", lambda state: None)
    assert w.deploy_steht_an({}, now=marke.stat().st_mtime + 60) is True


def test_leere_marke_gilt_als_unbekannt(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    marke.write_text("\n", encoding="utf-8")
    logs = []
    monkeypatch.setattr(w, "log", logs.append)
    monkeypatch.setattr(w, "save_state", lambda state: None)
    assert w.deploy_steht_an({}, path=str(marke), now=marke.stat().st_mtime) is True
    assert any("unbekannt" in zeile for zeile in logs)


def test_alte_marke_meldet_nur_einmal(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    jetzt = marke.stat().st_mtime + w.DEPLOY_PENDING_MAX_AGE_S + 1
    logs = []
    monkeypatch.setattr(w, "log", logs.append)
    monkeypatch.setattr(w, "save_state", lambda state: None)
    state = {}
    assert w.deploy_steht_an(state, path=str(marke), now=jetzt) is False
    assert w.deploy_steht_an(state, path=str(marke), now=jetzt) is False
    assert len(logs) == 1
    assert state["_deploypending"] == "alt:2222222"


def test_neue_marke_meldet_nach_alter_wieder(tmp_path, monkeypatch):
    marke = tmp_path / "pending"
    marke.write_text("2222222\n", encoding="utf-8")
    logs = []
    monkeypatch.setattr(w, "log", logs.append)
    monkeypatch.setattr(w, "save_state", lambda state: None)
    state = {"_deploypending": "alt:2222222"}
    assert w.deploy_steht_an(state, path=str(marke), now=marke.stat().st_mtime) is True
    assert len(logs) == 1
