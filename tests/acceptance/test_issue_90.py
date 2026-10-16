import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)

LEER = {"spend": 5.0, "max_budget": 5.0, "budget_reset_at": "2026-10-17T00:00:00+00:00"}
FREI = {"spend": 0.98, "max_budget": 10.0, "budget_reset_at": "2026-10-17T00:00:00+00:00"}
PROBLEM = "LiteLLM-Tagesbudget aufgebraucht (Reset 2026-10-17T00:00)"


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self, *args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _takt(monkeypatch, tmp_path, infos, key="issue7"):
    """main() mit Auftrag „issue7“; `infos` ist die Reihe der Antworten von key_info() (None = nicht erreichbar)."""
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "PREFIX", "")
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "pick_job", lambda state: (key, "Auftrag", None, None))
    monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
    monkeypatch.setattr(w, "check_issue_result", lambda k: None)
    antworten = iter(infos)
    monkeypatch.setattr(w, "key_info", lambda: next(antworten))
    probiert, gelaufen = [], []
    monkeypatch.setattr(w, "choose_model", lambda: probiert.append(1) or ("coder", []))
    monkeypatch.setattr(w, "run_agent", lambda prompt: gelaufen.append(prompt) or 0)
    return probiert, gelaufen


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_budget_problem_erkennt_ein_leeres_budget():
    assert w.budget_problem(LEER) == PROBLEM
    assert w.budget_problem(dict(LEER, spend=6.2)) == PROBLEM
    assert w.budget_problem({"spend": 5.0, "max_budget": 5.0}) == "LiteLLM-Tagesbudget aufgebraucht (Reset unbekannt)"


def test_budget_problem_ist_bei_unklarer_lage_still():
    assert w.budget_problem(FREI) is None
    assert w.budget_problem({"spend": 99.0, "max_budget": None}) is None     # kein Limit gesetzt
    assert w.budget_problem({}) is None
    assert w.budget_problem(None) is None                                     # key_info() ohne Antwort
    assert w.budget_problem({"spend": "viel", "max_budget": "5"}) is None


def test_key_info_fragt_den_eigenen_key_ab(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "testschluessel-123456")
    gesehen = []

    def fake(req, timeout=None):
        gesehen.append((req.full_url, req.get_header("Authorization")))
        return _Resp({"key": "x", "info": FREI})

    monkeypatch.setattr(w.urllib.request, "urlopen", fake)
    assert w.key_info() == FREI
    assert gesehen == [(f"{w.LITELLM}/key/info", "Bearer testschluessel-123456")]


@pytest.mark.parametrize("fehler", [urllib.error.URLError("weg"), TimeoutError(), ValueError("kein JSON")])
def test_key_info_gibt_bei_jedem_fehler_none(monkeypatch, fehler):
    def fake(req, timeout=None):
        raise fehler

    monkeypatch.setattr(w.urllib.request, "urlopen", fake)
    assert w.key_info() is None


def test_main_wartet_bei_leerem_budget_ohne_modellprobe_und_versuch(monkeypatch, tmp_path, capsys):
    probiert, gelaufen = _takt(monkeypatch, tmp_path, [LEER, LEER])
    assert w.main() == 0
    assert w.main() == 0
    out = capsys.readouterr().out
    assert out.count(f"⚠ Stephan: {PROBLEM}, warte. Versuch zählt nicht.") == 1     # einmal je Reset-Zeitraum
    assert probiert == [] and gelaufen == []
    assert "issue7" not in _state(tmp_path)


def test_main_laeuft_nach_dem_reset_von_selbst_weiter(monkeypatch, tmp_path, capsys):
    probiert, gelaufen = _takt(monkeypatch, tmp_path, [LEER, FREI])
    assert w.main() == 0 and gelaufen == []
    assert w.main() == 0
    assert len(gelaufen) == 1                      # derselbe Auftrag, kein Eingriff nötig
    assert _state(tmp_path)["issue7"] == 1
    assert "_budget" not in _state(tmp_path)


def test_main_meldet_ein_zweites_leeres_budget_wieder(monkeypatch, tmp_path, capsys):
    morgen = dict(LEER, budget_reset_at="2026-10-18T00:00:00+00:00")
    _takt(monkeypatch, tmp_path, [LEER, FREI, morgen])
    for _ in range(3):
        assert w.main() == 0
    out = capsys.readouterr().out
    assert out.count("⚠ Stephan: LiteLLM-Tagesbudget aufgebraucht") == 2


def test_main_laeuft_weiter_wenn_key_info_nicht_antwortet(monkeypatch, tmp_path):
    _, gelaufen = _takt(monkeypatch, tmp_path, [None])
    assert w.main() == 0
    assert len(gelaufen) == 1


def test_main_fragt_das_budget_nur_bei_vorhandener_arbeit(monkeypatch, tmp_path):
    _takt(monkeypatch, tmp_path, [])
    monkeypatch.setattr(w, "pick_job", lambda state: None)
    monkeypatch.setattr(w, "key_info", lambda: pytest.fail("ohne Arbeit keine Budgetabfrage"))
    assert w.main() == 0
