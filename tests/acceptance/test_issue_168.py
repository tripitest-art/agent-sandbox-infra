import importlib.util
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"


def _load():
    spec = importlib.util.spec_from_file_location("worker_tick_main_rot", _SANDBOX / "worker_tick.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["worker_tick_main_rot"] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load()

ROT = [{"conclusion": "failure", "headSha": "abcdef1234567890"}]
GRUEN = [{"conclusion": "success", "headSha": "1234567abcdef000"}]
JOB = ("issue5", "Auftragstext", None, None)


class FakeGH:
    def __init__(self, runs):
        self.runs = runs
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("run", "list"):
            if isinstance(self.runs, Exception):
                raise self.runs
            return self.runs
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _setup(monkeypatch, tmp_path, runs, rework=None, text=None, issue=JOB):
    fake = FakeGH(runs)
    monkeypatch.setattr(w, "gh", fake)
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "rework_job", lambda *a, **k: rework)
    monkeypatch.setattr(w, "text_fix_job", lambda *a, **k: text)
    monkeypatch.setattr(w, "issue_job", lambda *a, **k: issue)
    return fake


def test_roter_lauf_liefert_den_kurzen_hash(monkeypatch, tmp_path):
    fake = _setup(monkeypatch, tmp_path, ROT)
    assert w.main_red_sha() == "abcdef1"
    aufruf = fake.calls[0]
    assert aufruf[:2] == ("run", "list")
    assert "--workflow" in aufruf and "tests" in aufruf
    assert "--branch" in aufruf and "main" in aufruf


def test_nicht_rot_ist_none(monkeypatch, tmp_path):
    for runs in (GRUEN, [{"conclusion": "cancelled", "headSha": "abcdef1234"}],
                 [{"conclusion": "skipped", "headSha": "abcdef1234"}], [], None,
                 [{"conclusion": "failure"}], "kein json"):
        _setup(monkeypatch, tmp_path, runs)
        assert w.main_red_sha() is None, runs


def test_fehler_der_abfrage_blockiert_nicht_und_nennt_nur_die_klasse(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, RuntimeError("geheimer Text"))
    assert w.main_red_sha() is None
    out = capsys.readouterr().out
    assert "Warnung" in out and "RuntimeError" in out
    assert "geheimer Text" not in out


def test_rotes_main_sperrt_issue_auftraege_und_meldet_einmal(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path, ROT)
    state = {}
    assert w.find_job(state) is None
    erste = [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z]
    assert len(erste) == 1 and "abcdef1" in erste[0] and "main" in erste[0]
    assert w.find_job(state) is None
    assert [z for z in capsys.readouterr().out.splitlines() if "⚠ Stephan:" in z] == []


def test_gruenes_main_gibt_den_auftrag_frei_und_vergisst_den_hash(monkeypatch, tmp_path, capsys):
    fake = _setup(monkeypatch, tmp_path, ROT)
    state = {}
    assert w.find_job(state) is None
    fake.runs = GRUEN
    assert w.find_job(state) == JOB
    assert not any(k.startswith("_mainrot") for k in state)
    capsys.readouterr()
    fake.runs = ROT   # erneut rot: wird wieder gemeldet
    assert w.find_job(state) is None
    assert any("⚠ Stephan:" in z for z in capsys.readouterr().out.splitlines())


def test_nacharbeit_und_pr_text_laufen_trotz_rotem_main(monkeypatch, tmp_path):
    nach = ("pr7@2026-10-18T10:00:00Z", "Nacharbeit", 7, ("sha", "t", "b"))
    fake = _setup(monkeypatch, tmp_path, ROT, rework=nach)
    assert w.find_job({}) == nach
    text = ("text8@abcdef1", "Text", 8, ("sha", "t", "b"))
    fake = _setup(monkeypatch, tmp_path, ROT, text=text)
    assert w.find_job({}) == text
    assert fake.calls == []


def test_ohne_kandidat_wird_main_nicht_abgefragt(monkeypatch, tmp_path):
    fake = _setup(monkeypatch, tmp_path, ROT, issue=None)
    assert w.find_job({}) is None
    assert fake.calls == []
