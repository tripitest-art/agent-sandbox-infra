"""merge-nach-freigabe: wann gemergt wird und wann nicht.

Die Fälle hier sind die Grenze zwischen „die Reviewer-App hat freigegeben“ und „irgendwo
steht eine Freigabe“. Wer eine davon aufweicht, hebt die Identitätstrennung auf: dann
genügt wieder ein Label oder eine beliebige Review, um ungeprüften Code auf `main` zu
bringen.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "merge_nach_freigabe.py"
_spec = importlib.util.spec_from_file_location("merge_nach_freigabe_unit", _PATH)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)

BOT = "sandbox-agent-reviewer-app[bot]"
HEAD = "b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0"
PR = {"number": 12, "draft": False, "mergeable": True, "head": {"sha": HEAD, "ref": "feature/issue-7-test"}}


def _review(state="APPROVED", login=BOT, sha=HEAD, at="2026-10-24T20:00:00Z"):
    return {"state": state, "user": {"login": login}, "commit_id": sha, "submitted_at": at}


def _runs(*eintraege):
    return [{"name": name, "status": status, "conclusion": conclusion}
            for name, status, conclusion in eintraege]


GUTE_RUNS = _runs(
    ("acceptance-guard", "completed", "success"),
    ("tests", "completed", "success"),
    ("tests-als-root", "completed", "success"),
)


def _checks(state="success", runs=None):
    return {"state": state, "runs": GUTE_RUNS if runs is None else runs}


def _lauf(name, status, conclusion, start):
    """Check-Lauf mit Startzeit — die API liefert started_at, das Gate wählt danach den jüngsten."""
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": start}


def _decide(**kwargs):
    args = dict(pr=PR, reviews=[_review()], checks=_checks(), label_actor=BOT,
                trusted={"tripitest-art"}, bot=BOT)
    args.update(kwargs)
    return m.decide(**args)


# ---------------- Freigabe ----------------
def test_freigabe_des_bots_auf_dem_head_mergt():
    d = _decide()
    assert d.merge is True
    assert d.reasons == []


def test_freigabe_einer_anderen_identitaet_mergt_nicht():
    # Der Worker darf seinen eigenen PR nicht freigeben — auch nicht mit Label.
    d = _decide(reviews=[_review(login="worker-agent")])
    assert d.merge is False
    assert any("Keine Review von" in r for r in d.reasons)


def test_freigabe_auf_altem_sha_mergt_nicht():
    d = _decide(reviews=[_review(sha="a" * 40)])
    assert d.merge is False
    assert any("nicht für den aktuellen Head" in r for r in d.reasons)


def test_freigabe_ohne_sha_mergt_nicht():
    d = _decide(reviews=[_review(sha=None)])
    assert d.merge is False


def test_spaetere_aenderungsanforderung_schlaegt_die_freigabe():
    reviews = [_review(at="2026-10-24T20:00:00Z"),
               _review(state="CHANGES_REQUESTED", at="2026-10-24T21:00:00Z")]
    d = _decide(reviews=reviews)
    assert d.merge is False
    assert any("CHANGES_REQUESTED" in r or "Änderungsanforderung" in r for r in d.reasons)


def test_freigabe_nach_der_aenderungsanforderung_mergt():
    reviews = [_review(state="CHANGES_REQUESTED", at="2026-10-24T20:00:00Z"),
               _review(at="2026-10-24T21:00:00Z")]
    assert _decide(reviews=reviews).merge is True


def test_aenderungsanforderung_eines_anderen_kontos_blockiert():
    reviews = [_review(), _review(state="CHANGES_REQUESTED", login="tripitest-art")]
    d = _decide(reviews=reviews)
    assert d.merge is False
    assert any("tripitest-art" in r for r in d.reasons)


def test_aenderungsanforderung_auf_altem_sha_blockiert_nicht():
    reviews = [_review(), _review(state="CHANGES_REQUESTED", login="tripitest-art", sha="a" * 40)]
    assert _decide(reviews=reviews).merge is True


def test_kommentar_review_des_bots_mergt_nicht():
    d = _decide(reviews=[_review(state="COMMENTED")])
    assert d.merge is False


# ---------------- Zustand des PRs ----------------
def test_entwurf_mergt_nicht():
    d = _decide(pr={**PR, "draft": True})
    assert d.merge is False
    assert any("Entwurf" in r for r in d.reasons)


def test_unbekannte_mergebarkeit_mergt_nicht():
    # mergeable ist bei GitHub kurzzeitig null, solange die Prüfung läuft: nicht raten.
    for wert in (None, False):
        d = _decide(pr={**PR, "mergeable": wert})
        assert d.merge is False


def test_fehlender_head_mergt_nicht():
    d = _decide(pr={**PR, "head": {}})
    assert d.merge is False
    assert any("Head-SHA fehlt" in r for r in d.reasons)


# ---------------- Label-Herkunft ----------------
def test_label_vom_bot_ist_erlaubt():
    assert _decide(label_actor=BOT).merge is True


def test_label_von_vertrauenswuerdigem_konto_ist_erlaubt():
    assert _decide(label_actor="tripitest-art").merge is True


def test_label_von_fremdem_konto_mergt_nicht():
    d = _decide(label_actor="worker-agent")
    assert d.merge is False
    assert any("worker-agent" in r for r in d.reasons)


def test_label_ohne_herkunft_mergt_nicht():
    assert _decide(label_actor=None).merge is False


def test_ohne_trusted_actors_gilt_nur_der_bot():
    d = _decide(label_actor="tripitest-art", trusted=set())
    assert d.merge is False


# ---------------- Checks ----------------
def test_fehlender_guard_check_blockiert():
    d = _decide(checks=_checks(runs=_runs(("tests", "completed", "success"))))
    assert d.merge is False
    assert any("acceptance-guard" in r for r in d.reasons)


def test_roter_guard_check_blockiert():
    d = _decide(checks=_checks(runs=_runs(("acceptance-guard", "completed", "failure"))))
    assert d.merge is False


def test_laufender_check_blockiert():
    d = _decide(checks=_checks(runs=GUTE_RUNS + _runs(("tests", "in_progress", None))))
    assert d.merge is False
    assert any("in_progress" in r for r in d.reasons)


def test_roter_kombinierter_status_blockiert():
    d = _decide(checks=_checks(state="failure"))
    assert d.merge is False


def test_leere_checkliste_blockiert():
    d = _decide(checks=_checks(runs=[]))
    assert d.merge is False
    assert any("Keine fremden Check-Läufe" in r for r in d.reasons)


def test_eigener_laufender_check_wird_ignoriert():
    # Dieser Workflow prüft mit, während er selbst noch läuft: sich selbst als „nicht
    # fertig“ zu zählen würde jeden Merge dauerhaft blockieren.
    d = _decide(checks=_checks(runs=GUTE_RUNS + _runs((m.SELF_CHECK, "in_progress", None))))
    assert d.merge is True


# ---------------- main() ----------------
class _FakeGitHub:
    instances = []

    def __init__(self, repo, token, api="", graphql_url=""):
        self.calls = []
        self.fail_merge = False
        type(self).instances.append(self)

    def reviews(self, number):
        return [_review()]

    def pull(self, number):
        return PR

    def checks(self, sha):
        return _checks()

    def label_actor(self, number, label):
        return BOT

    def merge(self, number, sha):
        if self.fail_merge:
            raise m.MergeAbgelehnt("PUT /pulls/12/merge -> HTTP 405: Head moved")
        self.calls.append(("merge", number, sha))
        return {"merged": True}

    def delete_branch(self, ref):
        self.calls.append(("delete", ref))


def _main(monkeypatch, tmp_path, label="review-freigabe", gh_cls=_FakeGitHub, summary=None, pr=None):
    event = {"label": {"name": label}, "pull_request": pr or PR}
    pfad = tmp_path / "event.json"
    pfad.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(pfad))
    monkeypatch.setenv("GITHUB_REPOSITORY", "tripitest-art/agent-sandbox-infra")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GUARD_TRUSTED_ACTORS", "tripitest-art")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    if summary:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(m, "GitHub", gh_cls)
    gh_cls.instances.clear()
    return m.main()


def test_main_mergt_und_loescht_den_feature_branch(monkeypatch, tmp_path):
    assert _main(monkeypatch, tmp_path) == 0
    assert _FakeGitHub.instances[-1].calls == [("merge", 12, HEAD), ("delete", "feature/issue-7-test")]


def test_main_ohne_das_label_tut_nichts(monkeypatch, tmp_path):
    assert _main(monkeypatch, tmp_path, label="irgendwas") == 0
    assert _FakeGitHub.instances == []


def test_main_nutzt_den_frischen_zustand_statt_des_event_payloads(monkeypatch, tmp_path):
    # Im pull_request_target-Payload steht `mergeable` meist auf null. Wer dem Event
    # glaubt, blockiert jeden Merge; gefragt ist der API-Zustand.
    class Frisch(_FakeGitHub):
        instances = []

        def pull(self, number):
            return {**PR, "mergeable": True}

    assert _main(monkeypatch, tmp_path, gh_cls=Frisch, pr={**PR, "mergeable": None}) == 0


def test_main_mergt_nicht_wenn_die_api_nicht_mergebar_meldet(monkeypatch, tmp_path):
    class NichtMergebar(_FakeGitHub):
        instances = []

        def pull(self, number):
            return {**PR, "mergeable": False}

    assert _main(monkeypatch, tmp_path, gh_cls=NichtMergebar) == 1
    assert NichtMergebar.instances[-1].calls == []


def test_main_wartet_kurz_auf_die_mergebarkeit(monkeypatch, tmp_path):
    # Direkt nach einem Push liefert GitHub mergeable=null; ein zweiter Blick lohnt.
    monkeypatch.setattr(m.time, "sleep", lambda sekunden: None)

    class Spaet(_FakeGitHub):
        instances = []
        antworten = [{**PR, "mergeable": None}, {**PR, "mergeable": True}]

        def pull(self, number):
            return self.antworten.pop(0) if len(self.antworten) > 1 else self.antworten[0]

    assert _main(monkeypatch, tmp_path, gh_cls=Spaet) == 0


def test_main_ist_rot_wenn_nicht_gemergt_wurde(monkeypatch, tmp_path):
    class OhneFreigabe(_FakeGitHub):
        instances = []

        def reviews(self, number):
            return []

    assert _main(monkeypatch, tmp_path, gh_cls=OhneFreigabe) == 1
    assert OhneFreigabe.instances[-1].calls == []


def test_main_ist_rot_wenn_der_merge_abgelehnt_wird(monkeypatch, tmp_path):
    class Ablehnung(_FakeGitHub):
        instances = []

        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.fail_merge = True

    assert _main(monkeypatch, tmp_path, gh_cls=Ablehnung) == 1


def test_main_schreibt_die_begruendung_in_die_zusammenfassung(monkeypatch, tmp_path):
    ziel = tmp_path / "summary.md"

    class OhneFreigabe(_FakeGitHub):
        instances = []

        def reviews(self, number):
            return []

    assert _main(monkeypatch, tmp_path, gh_cls=OhneFreigabe, summary=ziel) == 1
    text = ziel.read_text(encoding="utf-8")
    assert "Nicht gemergt" in text
    assert "Keine Review von" in text


# ---------------- Warten auf fremde Checks ----------------
# Ein Label-Event startet auch den acceptance-guard neu. Ohne Warten sieht das Gate dessen
# frischen Lauf als offen und lehnt ab — genau das ist an PR #371 passiert.
class _SequenzGitHub(_FakeGitHub):
    """Liefert die Checks in der Reihenfolge, in der sie fertig werden."""

    FOLGE = []

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.check_abrufe = 0

    def checks(self, sha):
        self.check_abrufe += 1
        return type(self).FOLGE.pop(0) if type(self).FOLGE else _checks()


def test_wartet_bis_die_fremden_checks_fertig_sind(monkeypatch, tmp_path):
    laufend = _checks(runs=GUTE_RUNS + _runs(("acceptance-guard", "in_progress", None)))
    klasse = type("Wartend", (_SequenzGitHub,), {"instances": [], "FOLGE": [laufend, _checks()]})
    geschlafen = []
    monkeypatch.setattr(m.time, "sleep", lambda s: geschlafen.append(s))

    assert _main(monkeypatch, tmp_path, gh_cls=klasse) == 0
    assert klasse.instances[-1].calls == [("merge", 12, HEAD), ("delete", "feature/issue-7-test")]
    assert geschlafen, "das Gate muss zwischen den Abfragen warten"
    assert klasse.instances[-1].check_abrufe >= 2


def test_gibt_nach_der_frist_auf_und_mergt_nicht(monkeypatch, tmp_path):
    laufend = _checks(runs=GUTE_RUNS + _runs(("acceptance-guard", "in_progress", None)))
    klasse = type("Haengend", (_SequenzGitHub,), {"instances": [], "FOLGE": []})
    klasse.checks = lambda self, sha: laufend  # wird nie fertig

    # Uhr, die beim Warten fortschreitet, statt echt zu schlafen.
    zeit = {"t": 0.0}
    monkeypatch.setattr(m.time, "sleep", lambda s: zeit.__setitem__("t", zeit["t"] + s))
    monkeypatch.setattr(m.time, "monotonic", lambda: zeit["t"])
    monkeypatch.setenv("MERGE_CHECK_WAIT", "60")

    assert _main(monkeypatch, tmp_path, gh_cls=klasse) == 1
    assert klasse.instances[-1].calls == []


def test_der_eigene_check_zaehlt_nicht_als_offen(monkeypatch, tmp_path):
    eigene = _checks(runs=GUTE_RUNS + _runs((m.SELF_CHECK, "in_progress", None)))
    klasse = type("Eigener", (_SequenzGitHub,), {"instances": [], "FOLGE": [eigene]})
    geschlafen = []
    monkeypatch.setattr(m.time, "sleep", lambda s: geschlafen.append(s))

    assert _main(monkeypatch, tmp_path, gh_cls=klasse) == 0
    assert geschlafen == []
    assert klasse.instances[-1].check_abrufe == 1


def test_der_eigene_check_zaehlt_auch_aufgerufen_nicht_als_offen(monkeypatch, tmp_path):
    # Über workflow_call heißt der Check "<Job des Aufrufers> / merge-nach-freigabe".
    # Zählt er dann als fremd, blockiert das Gate jeden Merge durch sich selbst.
    eigene = _checks(runs=GUTE_RUNS + _runs((f"gate / {m.SELF_CHECK}", "in_progress", None)))
    klasse = type("Aufgerufen", (_SequenzGitHub,), {"instances": [], "FOLGE": [eigene]})
    geschlafen = []
    monkeypatch.setattr(m.time, "sleep", lambda s: geschlafen.append(s))

    assert _main(monkeypatch, tmp_path, gh_cls=klasse) == 0
    assert geschlafen == []
    assert klasse.instances[-1].check_abrufe == 1


def test_selbst_check_normalisiert_den_aufrufer():
    assert m.selbst_check(m.SELF_CHECK) is True
    assert m.selbst_check(f"gate / {m.SELF_CHECK}") is True
    assert m.selbst_check(f"  gate / {m.SELF_CHECK}  ") is True
    assert m.selbst_check("tests") is False
    assert m.selbst_check(m.SELF_CHECK + "-extra") is False
    assert m.selbst_check("") is False
    assert m.selbst_check(None) is False


# ---------------- Doppelte Läufe: der jüngste zählt ----------------
# Ein Label-Event löst den Guard erneut aus (cancel-in-progress). Die API listet beide Läufe;
# GitHub bewertet den jüngsten. An PR #380 war der ältere "cancelled" und das Gate hätte
# deshalb abgelehnt, obwohl der frische Lauf grün war.
def test_abgebrochener_alter_lauf_blockiert_nicht():
    runs = [
        _lauf("acceptance-guard", "completed", "cancelled", "2026-10-25T10:54:14Z"),
        _lauf("acceptance-guard", "completed", "success", "2026-10-25T10:54:24Z"),
        _lauf("tests", "completed", "success", "2026-10-25T10:54:13Z"),
        _lauf("tests-als-root", "completed", "success", "2026-10-25T10:54:13Z"),
    ]
    d = _decide(checks=_checks(runs=runs))
    assert d.merge is True, d.reasons


def test_juengerer_roter_lauf_blockiert_trotz_frueheren_gruenen():
    runs = [
        _lauf("acceptance-guard", "completed", "success", "2026-10-25T10:54:14Z"),
        _lauf("acceptance-guard", "completed", "failure", "2026-10-25T10:56:14Z"),
        _lauf("tests", "completed", "success", "2026-10-25T10:54:13Z"),
        _lauf("tests-als-root", "completed", "success", "2026-10-25T10:54:13Z"),
    ]
    d = _decide(checks=_checks(runs=runs))
    assert d.merge is False
    assert any("acceptance-guard" in r for r in d.reasons)


def test_juengster_lauf_zaehlt_auch_bei_umgekehrter_reihenfolge():
    runs = [
        _lauf("acceptance-guard", "completed", "success", "2026-10-25T10:54:24Z"),
        _lauf("acceptance-guard", "completed", "cancelled", "2026-10-25T10:54:14Z"),
        _lauf("tests", "completed", "success", "2026-10-25T10:54:13Z"),
        _lauf("tests-als-root", "completed", "success", "2026-10-25T10:54:13Z"),
    ]
    assert _decide(checks=_checks(runs=runs)).merge is True


# ---------------- Akteur-Namen: REST `name[bot]`, GraphQL `name` ----------------
# An PR #380 real aufgetreten: Das Label setzte `sandbox-agent-reviewer-app[bot]`, das
# Label-Event nannte als Akteur `sandbox-agent-reviewer-app` (GraphQL). Das Gate lehnte ab —
# damit konnte der Reviewer-Weg nie mergen.
def test_label_akteur_ohne_bot_suffix_wird_erkannt():
    d = _decide(label_actor="sandbox-agent-reviewer-app")
    assert d.merge is True, d.reasons


def test_label_akteur_mit_bot_suffix_bleibt_erlaubt():
    assert _decide(label_actor=BOT).merge is True


def test_fremder_akteur_bleibt_blockiert():
    d = _decide(label_actor="irgendwer")
    assert d.merge is False
    assert any("gesetzt" in r for r in d.reasons)


def test_review_von_bot_ohne_suffix_zaehlt_als_bot_review():
    reviews = [_review(login="sandbox-agent-reviewer-app")]
    assert _decide(reviews=reviews).merge is True


def test_akteur_key_normalisiert():
    assert m.akteur_key("Sandbox-Agent-Reviewer-App[bot]") == "sandbox-agent-reviewer-app"
    assert m.akteur_key(" tripitest-art ") == "tripitest-art"
    assert m.akteur_key(None) == ""
