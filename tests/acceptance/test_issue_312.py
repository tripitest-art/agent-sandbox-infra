import importlib.util
import itertools
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TICK_PATH = _ROOT / "sandbox" / "worker_tick.py"
_REVIEW_PATH = _ROOT / "sandbox" / "review_tick.py"
_SETTINGS_PATH = _ROOT / "sandbox" / "agent_settings.py"

# Teil des Vertrags: die freien Aliase und ihre Reihenfolge (identisch zur erlaubt-Liste).
FREIE_ALIASE = ("coder-mimo-free", "coder-deepseek-free", "coder-nemotron-free",
                "coder-minimax-free", "coder-beam-free")
OWNER_LOGIN = "tripitest-art"

_LADE_ZAEHLER = itertools.count()


def _issue(nummer, billig):
    """Issue-Kandidat wie `gh issue list --json number,title,body,author,labels`."""
    labels = ["worker-task", "bereit", "agent-qwen"]
    if billig:
        labels.append("worker:billig")
    return {"number": nummer, "title": f"Issue {nummer}",
            "author": {"login": OWNER_LOGIN},
            "labels": [{"name": n} for n in labels],
            "body": ("### Ziel\n\nKleine, einfache Aufgabe.\n\n"
                     "### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
                     "### Fertig, wenn\n\n- [ ] gruen\n")}


class FakeGH:
    """gh-Ersatz: die Billig- und die Normal-Abfrage werden getrennt abgefragt.

    `rolle` "billig": Der Labelwunsch `worker:billig` MUSS in der Issue-Liste stehen
    und der Ausschluss `-label:worker:billig` darf nie vorkommen; `rolle` "normal":
    genau umgekehrt. So beweist der Test das Routing über die echte `gh issue
    list`-Anfrage. Kandidaten- und `in-arbeit`-Liste sind getrennt; jeder Kandidat
    hat einen Autor und einen Betroffene-Dateien-Abschnitt (Regel 8)."""

    def __init__(self, rolle, billig=(), normal=()):
        self.rolle, self.billig, self.normal = rolle, list(billig), list(normal)
        self.edits = []
        self.angefragt = {"billig": False, "normal": False}

    def __call__(self, *args):
        if args[:2] == ("issue", "list"):
            if "--label" in args and "blockiert" in args:
                return []
            if "in-arbeit" in args:
                return []
            frage_billig = any(arg == "worker:billig" for arg in args)
            frage_normal = any("-label:worker:billig" in str(arg) for arg in args)
            if not (frage_billig or frage_normal):
                raise AssertionError(f"kein Billig-Routing in der Abfrage: {args}")
            if frage_billig and self.rolle == "normal":
                raise AssertionError("normaler Slot fragt nach worker:billig (verboten)")
            if frage_normal and self.rolle == "billig" and self.billig:
                raise AssertionError("Billig-Slot fragt mit Ausschluss von worker:billig trotz vorhandener Billig-Aufträge")
            self.angefragt["billig" if frage_billig else "normal"] = True
            return self.billig if frage_billig else self.normal
        if args[:2] == ("issue", "edit"):
            self.edits.append(args[2])
            return None
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Titel"}
        if args[:2] in (("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _lade(monkeypatch, tmp_path, slot, eintraege):
    """worker_tick frisch laden: Einstellungsdatei in tmp_path und Slot per Umgebung."""
    einst = tmp_path / "agent-settings.json"
    einst.write_text(json.dumps(eintraege), encoding="utf-8")
    monkeypatch.setenv("WORKER_SLOT", slot)
    monkeypatch.setenv("AGENT_SETTINGS", str(einst))
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.delenv("LITELLM_FALLBACKS", raising=False)
    name = f"worker_tick_billig_modul_{next(_LADE_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(name, _TICK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mod, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(mod, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(mod, "SLOT_DIR", str(tmp_path / "slots"))
    return mod


def test_billig_slot_nimmt_billig_issue(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "2", {"LITELLM_MODEL_BILLIG": "coder-mimo-free"})
    gh = FakeGH("billig", billig=[_issue(501, True)], normal=[_issue(502, False)])
    monkeypatch.setattr(w, "gh", gh)
    job = w.issue_job(state={}, now=1_000_000.0)
    assert job is not None, "issue_job fand das Billig-Issue nicht"
    key, task, pr_no, sha = job
    assert key == "issue501" and pr_no is None and sha is None
    assert "#501" in task


def test_billig_slot_faellt_auf_normales_issue_zurueck_wenn_kein_billig(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "2", {"LITELLM_MODEL_BILLIG": "coder-mimo-free"})
    # Nur ein normales Issue in der Welt; Slot 2 sucht zuerst billig (keins da)
    # und nimmt als Rückfallebene das normale Issue.
    gh = FakeGH("billig", normal=[_issue(502, False)])
    monkeypatch.setattr(w, "gh", gh)
    assert gh.normal, "Aufbau: das normale Issue existiert"
    job = w.issue_job(state={}, now=1_000_000.0)
    assert job is not None
    assert job[0] == "issue502"


def test_normaler_slot_laesst_billig_issue_liegen(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "1", {})
    gh = FakeGH("normal", billig=[_issue(501, True)], normal=[_issue(502, False)])
    monkeypatch.setattr(w, "gh", gh)
    job = w.issue_job(state={}, now=1_000_000.0)
    assert job is not None and job[0] == "issue502", "ohne Billig-Issue existiert: Slot 1 nimmt das normale Issue"


def test_normaler_slot_nimmt_billig_nie_ohne_kandidat(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "1", {})
    # Keine Normal-Kandidaten; der Fake würde eine worker:billig-Anfrage als Fehler quittieren.
    gh = FakeGH("normal", normal=[])
    monkeypatch.setattr(w, "gh", gh)
    assert w.issue_job(state={}, now=1_000_000.0) is None


def test_billig_modell_aus_einstellung_und_familienregel_6(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "2", {"LITELLM_MODEL_BILLIG": "coder-mimo-free"})
    assert w.BILLIG_MODEL == "coder-mimo-free"
    assert w.IS_BILLIG_SLOT is True
    assert w.WORKER == w.worker_name(w.BILLIG_MODEL), "PR-Zeile „Worker:“ nennt das Billig-Modell"
    spec = importlib.util.spec_from_file_location("agent_settings_billig", _SETTINGS_PATH)
    ag = importlib.util.module_from_spec(spec)
    sys.modules["agent_settings_billig"] = ag
    spec.loader.exec_module(ag)
    frei = ag.SPECS["LITELLM_MODEL_BILLIG"]["erlaubt"]
    assert "coder-mimo-free" in frei and "coder-deepseek-free" in frei
    rspec = importlib.util.spec_from_file_location("review_tick_billig", _REVIEW_PATH)
    rev = importlib.util.module_from_spec(rspec)
    sys.modules["review_tick_billig"] = rev
    rspec.loader.exec_module(rev)
    assert rev.model_family("reviewer-go") == "glm", "Reviewer-Stand: GLM-Familie (reviewer-go)"
    for alias in frei:
        familie = rev.model_family(w.worker_name(alias))
        assert familie is not None and familie != "glm", \
            f"Regel 6: {alias} liegt in Familie {familie!r} und darf nicht als Billig-Worker dienen"


def test_fallbackkette_ist_frei(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "2", {"LITELLM_MODEL_BILLIG": "coder-mimo-free"})
    kette = w.billig_fallbacks(w.BILLIG_MODEL)
    assert kette == [a for a in FREIE_ALIASE if a != w.BILLIG_MODEL], \
        "Reihenfolge der erlaubt-Liste ohne Primary"
    for alias in kette:
        assert alias not in w.BEZAHLTE_MODELLE, "keine bezahlten Ausweichmodelle im Billig-Slot"
    assert "coder-go" not in kette, "Abo-Alias coder-go frisst die Go-Quote, niemals im Billig-Slot"
    assert w.billig_fallbacks("") == []
    assert w.billig_fallbacks(None) == []
    assert w.billig_fallbacks("coder-nichtda") == []


def test_billig_slot_wartet_bei_modellausfall_ohne_versuch(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "2", {"LITELLM_MODEL_BILLIG": "coder-mimo-free"})
    gh = FakeGH("billig", billig=[_issue(501, True)])
    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "main_is_red", lambda state: False)
    monkeypatch.setattr(w, "main_is_red_local", lambda state: False)
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)

    def keins_netz(url, timeout=None):
        raise w.urllib.error.URLError("kein Netz im Test")

    monkeypatch.setattr(w.urllib.request, "urlopen", keins_netz)
    assert w.main() == 0
    assert gh.angefragt["billig"], "der Takt hat den Billig-Auftrag gesucht (nicht nur still abgebrochen)"
    assert gh.edits == [], "kein Claim (kein in-arbeit) beim Modellausfall"
    gespeichert = json.loads(Path(w.STATE).read_text(encoding="utf-8")) \
        if Path(w.STATE).exists() else {}
    assert "issue501" not in gespeichert, "Modellausfall darf keinen Versuch verbrauchen"


def test_billig_slot_wartet_bei_leerem_budget_ohne_versuch(monkeypatch, tmp_path, capsys):
    w = _lade(monkeypatch, tmp_path, "2", {"LITELLM_MODEL_BILLIG": "coder-mimo-free"})
    gh = FakeGH("billig", billig=[_issue(501, True)])
    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "main_is_red", lambda state: False)
    monkeypatch.setattr(w, "main_is_red_local", lambda state: False)
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)

    class Antwort:
        """`/key/info` meldet spend >= max_budget; keine Schlüssel, nur Zahlen (Regel 5)."""
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"info": {"spend": 55.5, "max_budget": 10.0,
                                        "budget_reset_at": "2026-10-24T00:00:00Z"}}).encode()

    monkeypatch.setattr(w.urllib.request, "urlopen", lambda url, timeout=None: Antwort())
    assert w.main() == 0
    assert gh.angefragt["billig"], "der Takt hat den Billig-Auftrag gesucht (nicht nur stille abgebrochen)"
    assert "⚠ Stephan:" in capsys.readouterr().out, "Budget-Ausschöpfung meldet über die Warnzeile"
    assert gh.edits == [], "kein Claim bei leerem Budget"
    gespeichert = json.loads(Path(w.STATE).read_text(encoding="utf-8")) \
        if Path(w.STATE).exists() else {}
    assert "issue501" not in gespeichert, "Budget-Halt darf keinen Versuch verbrauchen"
