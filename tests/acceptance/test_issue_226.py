import importlib.util
from pathlib import Path
import sys

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

_spec = importlib.util.spec_from_file_location("worker_tick_sched_test", _SANDBOX / "worker_tick.py")
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_sched_test"] = w
_spec.loader.exec_module(w)


class DummyProj:
    def __init__(self, name, workdir="/dummy"):
        self.name = name
        self.repo = f"org/{name}"
        self.workdir = workdir
        self.test_cmd = "pytest"
        self.hotspot = None
        self.hotspot_label = None


def test_ordered_projects_rotiert_nach_letztem_projekt():
    p1, p2, p3 = DummyProj("a"), DummyProj("b"), DummyProj("c")
    projs = [p1, p2, p3]

    assert [p.name for p in w.ordered_projects(projs, None)] == ["a", "b", "c"]
    assert [p.name for p in w.ordered_projects(projs, "unbekannt")] == ["a", "b", "c"]
    assert [p.name for p in w.ordered_projects(projs, "a")] == ["b", "c", "a"]
    assert [p.name for p in w.ordered_projects(projs, "b")] == ["c", "a", "b"]
    assert [p.name for p in w.ordered_projects(projs, "c")] == ["a", "b", "c"]


def test_pick_job_rotiert_ueber_projekte(monkeypatch, tmp_path):
    p1 = DummyProj("proj1", str(tmp_path / "p1"))
    p2 = DummyProj("proj2", str(tmp_path / "p2"))
    Path(p1.workdir).mkdir()
    Path(p2.workdir).mkdir()

    monkeypatch.setattr(w, "PROJECTS", [p1, p2])
    state = {}
    saved = []
    monkeypatch.setattr(w, "save_state", lambda s: saved.append(dict(s)))
    monkeypatch.setattr(w, "find_job", lambda s: ("job", "task", None, None))

    job1 = w.pick_job(state)
    assert job1 is not None
    assert state.get("_last_project") == "proj1"

    job2 = w.pick_job(state)
    assert job2 is not None
    assert state.get("_last_project") == "proj2"


def test_pick_job_ueberspringt_projekte_ohne_arbeit(monkeypatch, tmp_path):
    p1 = DummyProj("proj1", str(tmp_path / "p1"))
    p2 = DummyProj("proj2", str(tmp_path / "p2"))
    Path(p1.workdir).mkdir()
    Path(p2.workdir).mkdir()

    monkeypatch.setattr(w, "PROJECTS", [p1, p2])
    state = {"_last_project": "proj1"}

    def fake_find(s):
        if w.PROJECT == "proj2":
            return None
        return ("job_proj1", "task", None, None)

    monkeypatch.setattr(w, "find_job", fake_find)
    monkeypatch.setattr(w, "save_state", lambda s: None)

    job = w.pick_job(state)
    assert job == ("job_proj1", "task", None, None)
    assert state.get("_last_project") == "proj1"
    # Wenn aber proj2 Arbeit hat, muss nach proj1 zwingend proj2 drankommen
    monkeypatch.setattr(w, "find_job", lambda s: (f"job_{w.PROJECT}", "task", None, None))
    job_next = w.pick_job(state)
    assert job_next[0] == "job_proj2"
    assert state.get("_last_project") == "proj2"
