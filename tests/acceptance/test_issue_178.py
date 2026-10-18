import importlib.util
import json
import sys
import urllib.error
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load("worker_tick")
r = _load("review_tick")            # nutzt dasselbe worker_tick-Modul (r.worker_tick is w)

SHA = "a" * 40
LABEL = "review-anfordern"
OK_REVIEW = "**Freigegeben**\n\n### Muss\nkeine\n\n### Kann\n- Import nach oben\n\n### Geprüft\n- alles"
CHECKS = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": "2026-10-18T08:00:00Z"}
          for n in ("tests", "acceptance-guard")]
FEHLER_ZEILE = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                           "duration_ms": 284, "num_turns": 0,
                           "error": {"message": "[API Error: 429 Budget has been exceeded! Key=worker (sk-...abcd)]"}})


def _ereignisse(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---- Hilfsfunktionen im Worker-Takt ----

def test_run_cost_ist_der_unterschied_oder_none():
    assert w.run_cost(1.0, 1.25) == 0.25
    assert w.run_cost(0.1, 0.3) == 0.2                     # auf sechs Stellen gerundet, kein 0.19999999999999998
    assert w.run_cost(2.0, 2.0) == 0.0
    for vorher, nachher in ((None, 1.0), (1.0, None), (None, None), (2.0, 1.0), ("1", 2.0), (True, 2.0)):
        assert w.run_cost(vorher, nachher) is None


def test_spend_of_liest_spend_aus_der_key_info():
    assert w.spend_of({"spend": 1.5, "max_budget": 10}) == 1.5
    assert w.spend_of({"spend": 3}) == 3.0
    for info in (None, {}, {"spend": None}, {"spend": "viel"}, {"spend": True}, "kein dict", []):
        assert w.spend_of(info) is None


def _ev(slot, art, ts):
    return {"ts": ts, "event": art, "slot": slot}


def test_run_overlap_erkennt_laeufe_anderer_slots_im_zeitraum():
    start, ende = "2026-10-18T10:00:00+00:00", "2026-10-18T10:10:00+00:00"
    innen = [_ev("2", "run_started", "2026-10-18T10:03:00+00:00"), _ev("2", "run_finished", "2026-10-18T10:05:00+00:00")]
    davor_offen = [_ev("2", "run_started", "2026-10-18T09:40:00+00:00")]
    ueberlappt_ende = [_ev("2", "run_started", "2026-10-18T09:50:00+00:00"), _ev("2", "run_finished", "2026-10-18T10:02:00+00:00")]
    assert w.run_overlap(innen, "1", start, ende) is True
    assert w.run_overlap(davor_offen, "1", start, ende) is True        # lief schon und läuft noch
    assert w.run_overlap(ueberlappt_ende, "1", start, ende) is True


def test_run_overlap_ignoriert_eigenen_slot_review_und_fremde_zeiten():
    start, ende = "2026-10-18T10:00:00+00:00", "2026-10-18T10:10:00+00:00"
    eigen = [_ev("1", "run_started", "2026-10-18T10:01:00+00:00"), _ev("1", "run_finished", "2026-10-18T10:09:00+00:00")]
    review = [{"ts": "2026-10-18T10:02:00+00:00", "event": "review_posted", "slot": "review"}]
    davor = [_ev("2", "run_started", "2026-10-18T09:00:00+00:00"), _ev("2", "run_finished", "2026-10-18T09:30:00+00:00")]
    danach = [_ev("2", "run_started", "2026-10-18T10:11:00+00:00")]
    for events in (eigen, review, davor, danach, []):
        assert w.run_overlap(events, "1", start, ende) is False, events


def test_read_audit_tail_ueberspringt_kaputtes_und_kuerzt(tmp_path, monkeypatch):
    f = tmp_path / "audit.jsonl"
    monkeypatch.setattr(w, "AUDIT_LOG", str(f))
    assert w.read_audit_tail() == []                       # Datei fehlt
    zeilen = [json.dumps({"event": f"e{i}"}) for i in range(5)]
    f.write_text("\n".join(zeilen[:2] + ["{kaputt"] + zeilen[2:]) + "\n", encoding="utf-8")
    assert [e["event"] for e in w.read_audit_tail()] == ["e0", "e1", "e2", "e3", "e4"]
    assert [e["event"] for e in w.read_audit_tail(limit=2)] == ["e3", "e4"]


# ---- main(): Kosten je Lauf im Audit-Log ----
# Der Spend nach dem Lauf kommt aus der Budgetprüfung des nächsten Taktes (LiteLLM bucht gebündelt, bis dahin
# ist der Wert stabil), ohne zusätzliche Abfrage an /key/info.

class _Lauf:
    def __init__(self, monkeypatch, tmp_path, slot="1"):
        self.f = tmp_path / "audit.jsonl"
        self.state = tmp_path / "state.json"
        self.info = {"spend": 10.0, "max_budget": 50.0}
        self.job = ("issue7", "Auftrag", None, None)
        self.fragen = 0
        monkeypatch.setattr(w, "AUDIT_LOG", str(self.f))
        monkeypatch.setattr(w, "WORKER_SLOT", slot)
        monkeypatch.setattr(w, "STATE", str(self.state))
        monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
        monkeypatch.setattr(w, "PREFIX", "")
        monkeypatch.setattr(w, "BACKEND", "litellm")
        monkeypatch.setattr(w, "ACTIVE_MODEL", "coder")
        monkeypatch.setattr(w, "litellm_ready", lambda: None)
        monkeypatch.setattr(w, "key_info", self._key_info)
        monkeypatch.setattr(w, "pick_job", lambda state: self.job)
        monkeypatch.setattr(w, "choose_model", lambda: ("coder", []))
        monkeypatch.setattr(w, "gh", lambda *a, **k: "")
        monkeypatch.setattr(w, "prepare_workdir", lambda: None)
        monkeypatch.setattr(w, "ensure_pre_push_hook", lambda: None)
        monkeypatch.setattr(w, "slot_status", lambda *a, **k: None)
        monkeypatch.setattr(w, "check_issue_result", lambda k: None)
        self.agent = lambda prompt: 0
        monkeypatch.setattr(w, "run_agent", lambda prompt: self.agent(prompt))

    def _key_info(self):
        self.fragen += 1
        return self.info

    def main(self):
        assert w.main() == 0

    def ereignisse(self, art=None):
        return [e for e in _ereignisse(self.f) if art is None or e["event"] == art]

    def offen(self):
        return json.loads(self.state.read_text(encoding="utf-8")).get("_kosten_offen")


def test_lauf_schreibt_start_und_ende_und_merkt_sich_den_spend(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.main()
    ev = [e for e in lauf.ereignisse() if e["event"] in ("run_started", "run_finished", "run_cost")]
    assert [e["event"] for e in ev] == ["run_started", "run_finished"]        # Kosten folgen beim nächsten Takt
    start, ende = ev
    assert start["key"] == "issue7" and start["spend_vorher"] == 10.0
    assert ende["key"] == "issue7" and ende["exit_code"] == 0 and ende["anbieterfehler"] is False
    assert ende["dauer_s"] >= 0 and ende["active_model"] == "coder" and ende["slot"] == "1"
    offen = lauf.offen()
    assert offen["key"] == "issue7" and offen["spend_vorher"] == 10.0 and offen["start"] and offen["ende"]
    assert lauf.fragen == 1                                # nur die Budgetprüfung, keine zusätzliche Abfrage


def test_der_naechste_takt_schreibt_die_kosten_des_vorigen_laufs(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.main()
    lauf.info = {"spend": 10.5, "max_budget": 50.0}
    lauf.main()
    kosten = lauf.ereignisse("run_cost")
    assert len(kosten) == 1
    k = kosten[0]
    assert k["key"] == "issue7" and k["spend_vorher"] == 10.0 and k["spend_nachher"] == 10.5
    assert k["kosten"] == 0.5 and k["parallel"] is False and k["run_ende"]
    # der zweite Lauf hat seinen eigenen Anfangswert und ist jetzt der offene Lauf
    assert lauf.ereignisse("run_started")[1]["spend_vorher"] == 10.5
    assert lauf.offen()["spend_vorher"] == 10.5


def test_ein_anderer_slot_im_zeitraum_macht_den_lauf_parallel(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.main()
    with open(lauf.f, "a", encoding="utf-8") as fh:        # Slot 2 hat einen Lauf, der begann und noch nicht endete
        fh.write(json.dumps({"ts": "2000-01-01T00:00:00+00:00", "event": "run_started", "slot": "2", "key": "issue9"}) + "\n")
    lauf.info = {"spend": 10.9, "max_budget": 50.0}
    lauf.job = None                                        # kein neuer Auftrag: der Takt rechnet nur ab
    lauf.main()
    k = lauf.ereignisse("run_cost")[0]
    assert k["parallel"] is True and k["kosten"] == 0.9    # der Unterschied bleibt als Obergrenze stehen


def test_ein_takt_ohne_auftrag_rechnet_den_offenen_lauf_ab(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.main()
    lauf.job = None
    lauf.info = {"spend": 10.25, "max_budget": 50.0}
    lauf.main()
    assert [e["kosten"] for e in lauf.ereignisse("run_cost")] == [0.25]
    assert lauf.offen() is None


def test_ein_takt_ohne_auftrag_und_ohne_offenen_lauf_fragt_nicht_nach(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.job = None
    lauf.main()
    assert lauf.fragen == 0 and lauf.ereignisse("run_cost") == []


def test_ohne_lesbaren_spend_bleibt_der_lauf_offen_bis_er_lesbar_ist(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.main()
    lauf.job = None
    lauf.info = None                                       # key_info nicht erreichbar
    lauf.main()
    assert lauf.ereignisse("run_cost") == [] and lauf.offen() is not None
    lauf.info = {"spend": 10.1, "max_budget": 50.0}
    lauf.main()
    assert [e["kosten"] for e in lauf.ereignisse("run_cost")] == [0.1] and lauf.offen() is None


def test_ein_lauf_ohne_spend_vorher_ergibt_kosten_none(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    lauf.info = {"max_budget": 50.0}                       # kein spend im Ergebnis
    lauf.main()
    assert lauf.ereignisse("run_started")[0]["spend_vorher"] is None
    lauf.job = None
    lauf.info = {"spend": 12.0, "max_budget": 50.0}
    lauf.main()
    k = lauf.ereignisse("run_cost")[0]
    assert k["kosten"] is None and k["spend_nachher"] == 12.0


def test_lauf_mit_anbieterfehler_wird_trotzdem_protokolliert(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)

    def agent(prompt):
        w.log_stream_line(FEHLER_ZEILE)
        return 1

    lauf.agent = agent
    lauf.main()
    ende = lauf.ereignisse("run_finished")[0]
    assert ende["exit_code"] == 1 and ende["anbieterfehler"] is True
    assert lauf.offen() is not None                        # die Kosten des fehlgeschlagenen Laufs gibt es auch


def test_ein_unlesbares_audit_log_stoppt_den_lauf_nicht(monkeypatch, tmp_path):
    lauf = _Lauf(monkeypatch, tmp_path)
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "gibt" / "es" / "nicht" / "audit.jsonl"))
    lauf.main()                                            # kein Absturz
    lauf.job = None
    lauf.info = {"spend": 10.3, "max_budget": 50.0}
    lauf.main()


# ---- Review-Takt: Kosten je gepostetem Review ----

@pytest.fixture
def review_env(monkeypatch, tmp_path):
    p = r.projects.default_projects()[0]
    monkeypatch.setattr(r, "PROJECTS", [p])
    monkeypatch.setattr(r, "REPO", p.repo)
    monkeypatch.setattr(r, "REVIEW_SKILL", p.review_skill)
    monkeypatch.setattr(r, "PROJECT_AUTO_MERGE", False)
    monkeypatch.setattr(r, "REQUIRED_CHECKS", ("tests", "acceptance-guard"))
    monkeypatch.setattr(r, "BASE_STATE", str(tmp_path / "rs.json"))
    monkeypatch.setattr(r, "BASE_DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "STATE", str(tmp_path / "rs.json"))
    monkeypatch.setattr(r, "DRAFT_DIR", str(tmp_path / "reviews"))
    monkeypatch.setattr(r, "REVIEW_KEY", "test-key")
    monkeypatch.setattr(r, "REVIEWER_NAME", "GLM")
    monkeypatch.setattr(r, "REVIEW_MODEL", "coder-glm")
    monkeypatch.setattr(r, "REVIEW_MODEL_BIG", "longctx-lite")
    monkeypatch.setattr(r, "POST", True)
    monkeypatch.setattr(r, "AUTO_MERGE", True)
    monkeypatch.setattr(r.worker_tick, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(r.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("kein Netz")))
    return tmp_path / "audit.jsonl"


def _review_takt(monkeypatch, antworten, checks=CHECKS):
    pr = {"number": 7, "title": "PR 7", "body": "Was und warum\n\nWorker: DeepSeek (LiteLLM coder)\n",
          "headRefName": "feature/manuell-7", "headRefOid": SHA, "isCrossRepository": False, "isDraft": False,
          "labels": [{"name": LABEL}], "reviews": [], "commits": [{"committedDate": "2026-10-18T07:00:00Z"}],
          "statusCheckRollup": checks}
    seen = {"calls": [], "posted": []}

    def gh_json(*args):
        if args[:2] == ("pr", "list"):
            return [pr]
        if args[:2] == ("pr", "view"):
            return {"files": [{"path": "a.py", "additions": 1, "deletions": 0}]}
        if args[:2] == ("issue", "list"):
            return []
        raise AssertionError(args)

    def call_llm(model, messages):
        seen["calls"].append(model)
        return antworten[model]

    monkeypatch.setattr(r, "gh_json", gh_json)
    monkeypatch.setattr(r, "gh_text", lambda *a, **k: "diff --git a/a.py b/a.py\n+x\n")
    monkeypatch.setattr(r, "repo_file", lambda p: "### Reviewer\nregel\n## Goldene Regeln\ng\n## Schritt 5\nf\n## Schritt 7\nx")
    monkeypatch.setattr(r, "call_llm", call_llm)
    monkeypatch.setattr(r, "post_review", lambda n, t: seen["posted"].append(t))
    monkeypatch.setattr(r, "post_review_status", lambda sha, verdict: True)
    monkeypatch.setattr(r, "to_stephan", lambda n, key, reason, state: None)
    assert r.run_once() == 0
    return seen


def test_review_posted_nennt_die_kosten_des_modellaufrufs(monkeypatch, review_env):
    usage = {"prompt_tokens": 12000, "completion_tokens": 900, "cost": 0.0099518, "finish_reason": "stop"}
    _review_takt(monkeypatch, {"coder-glm": (OK_REVIEW, usage)})
    ev = [e for e in _ereignisse(review_env) if e["event"] == "review_posted"]
    assert len(ev) == 1
    e = ev[0]
    assert e["pr"] == 7 and e["urteil"] == "Freigegeben" and e["slot"] == "review"
    assert e["model"] == "coder-glm" and e["modelle"] == ["coder-glm"] and e["ohne_modell"] is False
    assert e["kosten"] == pytest.approx(0.0099518) and e["prompt_tokens"] == 12000 and e["completion_tokens"] == 900


def test_nach_dem_ausweichen_zaehlen_die_kosten_beider_aufrufe(monkeypatch, review_env):
    erster = ("", {"prompt_tokens": 13000, "completion_tokens": 16000, "cost": 0.0099, "finish_reason": "length"})
    zweiter = (OK_REVIEW, {"prompt_tokens": 13000, "completion_tokens": 216, "cost": 0.0002, "finish_reason": "stop"})
    _review_takt(monkeypatch, {"coder-glm": erster, "longctx-lite": zweiter})
    e = [x for x in _ereignisse(review_env) if x["event"] == "review_posted"][0]
    assert e["modelle"] == ["coder-glm", "longctx-lite"] and e["model"] == "longctx-lite"
    assert e["kosten"] == pytest.approx(0.0101) and e["completion_tokens"] == 16216


def test_ohne_kostenangabe_im_usage_ist_kosten_none(monkeypatch, review_env):
    _review_takt(monkeypatch, {"coder-glm": (OK_REVIEW, {"prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "stop"})})
    e = [x for x in _ereignisse(review_env) if x["event"] == "review_posted"][0]
    assert e["kosten"] is None


def test_rote_checks_ohne_modellaufruf_kosten_null(monkeypatch, review_env):
    rot = [dict(c, conclusion="FAILURE") if c["name"] == "tests" else c for c in CHECKS]
    seen = _review_takt(monkeypatch, {}, checks=rot)
    assert seen["calls"] == [] and len(seen["posted"]) == 1
    e = [x for x in _ereignisse(review_env) if x["event"] == "review_posted"][0]
    assert e["ohne_modell"] is True and e["kosten"] == 0.0 and e["modelle"] == []
    assert e["urteil"] == "Änderungen nötig"


def test_ein_entwurf_ohne_posten_schreibt_kein_ereignis(monkeypatch, review_env):
    monkeypatch.setattr(r, "POST", False)
    _review_takt(monkeypatch, {"coder-glm": (OK_REVIEW, {"cost": 0.01, "finish_reason": "stop"})})
    assert [e for e in _ereignisse(review_env) if e["event"] == "review_posted"] == []


def test_ein_unlesbares_audit_log_haelt_das_review_nicht_auf(monkeypatch, review_env, tmp_path):
    monkeypatch.setattr(r.worker_tick, "AUDIT_LOG", str(tmp_path / "gibt" / "es" / "nicht" / "a.jsonl"))
    seen = _review_takt(monkeypatch, {"coder-glm": (OK_REVIEW, {"cost": 0.01, "finish_reason": "stop"})})
    assert len(seen["posted"]) == 1
