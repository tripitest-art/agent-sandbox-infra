import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "autodeploy.py"
_spec = importlib.util.spec_from_file_location("autodeploy_preflight_wt", _PATH)
ad = importlib.util.module_from_spec(_spec)
sys.modules["autodeploy_preflight_wt"] = ad
_spec.loader.exec_module(ad)

ALT, NEU = "1111111", "2222222"


def _cp(cmd, rc=0, out=""):
    return subprocess.CompletedProcess(cmd, rc, out, "")


class Git:
    """Ersatz für `run`: merkt alle Befehle; Testlauf und worktree add steuerbar."""

    def __init__(self, test_rc=0, test_out="", add_rc=0):
        self.test_rc, self.test_out, self.add_rc = test_rc, test_out, add_rc
        self.cmds = []

    def __call__(self, cmd):
        c = list(cmd)
        self.cmds.append(c)
        if c[:3] == ["git", "rev-parse", "--short"]:
            return _cp(cmd, 0, (ALT if "HEAD" in c else NEU) + "\n")
        if c[:3] == ["git", "worktree", "add"]:
            return _cp(cmd, self.add_rc)
        if "test-runner" in c:
            return _cp(cmd, self.test_rc, self.test_out)
        return _cp(cmd, 0)

    def index(self, praefix):
        return next(i for i, c in enumerate(self.cmds) if c[:len(praefix)] == praefix)

    def hat(self, praefix):
        return any(c[:len(praefix)] == praefix for c in self.cmds)


def _lauf(tmp_path, git, wt):
    notes = []
    (tmp_path / ".deploy-state.json").write_text(json.dumps({"last_good": ALT}), encoding="utf-8")
    res = ad.deploy_once(tmp_path, git, notes.append, is_busy=lambda: False,
                         test_cmd=["test-runner"], install_cmd=["installer"], preflight_dir=wt)
    state = json.loads((tmp_path / ".deploy-state.json").read_text(encoding="utf-8"))
    return res, notes, state


def test_vorgabe_liegt_ausserhalb_des_klons():
    assert ad.PREFLIGHT_DIR
    assert not ad.PREFLIGHT_DIR.startswith(ad.INFRA_DIR)
    assert all(not ad.PREFLIGHT_DIR.startswith(p) for p in ad.BLOCKED_PATHS)


def test_gruen_testet_im_worktree_und_zieht_erst_danach(tmp_path):
    wt = str(tmp_path / "wt")
    git = Git(test_rc=0)
    res, notes, state = _lauf(tmp_path, git, wt)
    assert res.status == "ausgerollt"
    i_add = git.index(["git", "worktree", "add", "--detach", wt, NEU])
    i_test = git.index(["env", "-C", wt, "test-runner"])
    i_pull = git.index(["git", "pull", "--ff-only"])
    i_install = git.index(["installer"])
    assert i_add < i_test < i_pull < i_install
    removes = [i for i, c in enumerate(git.cmds) if c == ["git", "worktree", "remove", "--force", wt]]
    assert any(i_test < i < i_pull for i in removes), "Worktree wird vor dem pull wieder entfernt"
    assert ["test-runner"] not in git.cmds, "kein Testlauf im Klon"
    assert state["last_good"] == NEU and state.get("bad") is None


def test_rot_laesst_den_klon_unberuehrt(tmp_path):
    wt = str(tmp_path / "wt")
    out = "FAILED tests/test_a.py::test_eins - assert 1 == 2\n1 failed\n"
    git = Git(test_rc=1, test_out=out)
    res, notes, state = _lauf(tmp_path, git, wt)
    assert res.status == "zurueckgerollt"
    assert res.detail == "Tests fehlgeschlagen (Exit 1): tests/test_a.py::test_eins"
    assert not git.hat(["git", "pull"]), "bei Rot kein pull"
    assert not git.hat(["git", "reset", "--hard"]), "bei Rot nichts zurückzusetzen"
    assert not git.hat(["installer"])
    letzte_removes = [i for i, c in enumerate(git.cmds) if c == ["git", "worktree", "remove", "--force", wt]]
    assert letzte_removes[-1] > git.index(["env", "-C", wt, "test-runner"]), "auch bei Rot aufgeräumt"
    assert state["bad"] == NEU and state["last_good"] == ALT
    assert any(n.startswith("⚠ Stephan:") and "tests/test_a.py::test_eins" in n for n in notes)


def test_worktree_nicht_anlegbar_ist_fehler_ohne_markierung(tmp_path):
    wt = str(tmp_path / "wt")
    git = Git(add_rc=128)
    res, notes, state = _lauf(tmp_path, git, wt)
    assert res.status == "fehler"
    assert res.detail == "Pre-Flight-Worktree nicht anlegbar (Exit 128)"
    assert not git.hat(["git", "pull"])
    assert not any("test-runner" in c for c in git.cmds)
    assert state.get("bad") is None
    assert any(n.startswith("⚠ Stephan:") for n in notes)


def test_ohne_preflight_dir_bleibt_alles_beim_alten(tmp_path):
    git = Git(test_rc=0)
    notes = []
    (tmp_path / ".deploy-state.json").write_text(json.dumps({"last_good": ALT}), encoding="utf-8")
    res = ad.deploy_once(tmp_path, git, notes.append, is_busy=lambda: False,
                         test_cmd=["test-runner"], install_cmd=["installer"])
    assert res.status == "ausgerollt"
    assert ["test-runner"] in git.cmds
    assert not git.hat(["git", "worktree"])


def test_main_reicht_den_worktree_durch(monkeypatch, tmp_path):
    wt = str(tmp_path / "wt")
    monkeypatch.setattr(ad, "PREFLIGHT_DIR", wt)
    monkeypatch.setattr(ad, "INFRA_DIR", str(tmp_path / "klon"))
    monkeypatch.setattr(ad, "_log", lambda msg: None)
    monkeypatch.setattr(ad, "mark_pending", lambda repo, run, result: None)
    gesehen = {}

    def fake_deploy(repo, run, notify, **kw):
        gesehen.update(kw)
        return ad.DeployResult("unveraendert", ALT, ALT, "kein neuer Stand")

    monkeypatch.setattr(ad, "deploy_once", fake_deploy)
    assert ad.main(run=lambda cmd: _cp(cmd, 0)) == 0
    assert gesehen["preflight_dir"] == wt
    assert f"WorkingDirectory={wt}" in gesehen["test_cmd"]
