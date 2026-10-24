import importlib.util
import json
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_notiz", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_notiz"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")
FAILED = "FAILED tests/acceptance/test_issue_7.py::test_a - assert 1 == 2"


class FakeGH:
    def __init__(self, issues):
        self.issues = list(issues)
        self.kommentare = []

    def __call__(self, *args, **kwargs):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] == ("issue", "edit"):
            return None
        if args[:2] == ("issue", "comment"):
            self.kommentare.append((args[2], args[args.index("--body") + 1]))
            return None
        if args[:2] in (("pr", "list"), ("run", "list")) or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _tool_result(text):
    return json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": text}]}})


def test_fehlerzeilen_letzte_treffer_gekuerzt_und_ohne_geheimnis(monkeypatch):
    monkeypatch.setenv("LITELLM_API_KEY", "sk-geheim-0123456789abcdef")
    text = ("....F\n>       assert f(1) == 2\nE       assert 3 == 2\n"
            + "\n".join(f"FAILED tests/test_x.py::test_{i} - AssertionError" for i in range(20))
            + "\n1 failed, 4 passed\n")
    z = w.fehlerzeilen(text)
    assert w.NOTIZ_ZEILEN == 15 and len(z) == 15
    assert z[-1] == "FAILED tests/test_x.py::test_19 - AssertionError"
    assert w.fehlerzeilen("5 passed in 0.1s\n") == []
    assert len(w.fehlerzeilen("E   " + "x" * 500)[0]) == 200
    assert "sk-geheim-0123456789abcdef" not in "\n".join(w.fehlerzeilen("E   key=sk-geheim-0123456789abcdef"))


def test_versuch_notiz_gruende_und_erfolg(monkeypatch):
    monkeypatch.setattr(w, "RUN_TIMEOUT", 1800)
    monkeypatch.setattr(w, "NO_PROGRESS_MINUTEN", 15)
    assert w.versuch_notiz(0, True, ["E   x"], False) is None
    assert w.versuch_notiz(124, False, [], True) == (
        "Previous attempt failed: stopped by the watchdog (no file change for 15 minutes). "
        "Start changing files earlier.")
    assert w.versuch_notiz(124, False, [], False) == "Previous attempt failed: time limit of 30 minutes reached."
    assert w.versuch_notiz(1, True, [], False) == "Previous attempt failed: agent exited with code 1."
    ohne_pr = w.versuch_notiz(0, False, [FAILED], False)
    assert ohne_pr.startswith("Previous attempt failed: the run ended without a pull request")
    assert ohne_pr.endswith("\nLast test errors:\n" + FAILED)
    assert w.notiz_block("X") == "----- Previous attempt -----\nX\n----- End previous attempt -----"


def test_stream_merkt_die_letzte_fehlerausgabe(monkeypatch):
    monkeypatch.setattr(w, "AGENT_FEHLERZEILEN", [])
    w.log_stream_line(_tool_result(FAILED + "\n1 failed"))
    assert w.AGENT_FEHLERZEILEN == [FAILED]
    w.log_stream_line(_tool_result("README.md\nsandbox"))
    assert w.AGENT_FEHLERZEILEN == [FAILED]
    opencode = json.dumps({"type": "tool_use", "part": {"tool": "bash", "state": {
        "input": {"command": "pytest"}, "output": "ERROR tests/b.py - ImportError", "status": "completed"}}})
    w.log_stream_line(opencode)
    assert w.AGENT_FEHLERZEILEN == ["ERROR tests/b.py - ImportError"]


def test_waechter_merkt_sich_den_abbruch(monkeypatch):
    monkeypatch.setattr(w, "WAECHTER_BEENDET", False)
    monkeypatch.setattr(w, "NO_PROGRESS_MINUTEN", 15)
    monkeypatch.setattr(w, "workdir_zustand", lambda cwd: "gleich")

    class Proc:
        beendet = False

        def terminate(self):
            self.beendet = True

    proc = Proc()
    assert w.watchdog_pruefen(proc, "gleich", 0.0, "/kein/ordner", jetzt=15 * 60 + 1) is True
    assert proc.beendet and w.WAECHTER_BEENDET is True


def test_main_notiz_kommentar_naechster_auftrag_und_aufraeumen(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "MAX_TRIES", 3)
    monkeypatch.setattr(w, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(w, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(w, "SLOT_DIR", str(tmp_path / "slots"))
    kandidat = {"number": 7, "title": "Test Issue", "body": ISSUE_BODY,
                "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}
    gh = FakeGH([kandidat])
    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "choose_model", lambda *a: ("coder", []))
    ergebnisse = iter([False, True])
    monkeypatch.setattr(w, "open_pr", lambda n: next(ergebnisse))
    prompts = []

    def lauf_1(prompt):
        prompts.append(prompt)
        w.AGENT_FEHLERZEILEN = [FAILED]
        return 1

    monkeypatch.setattr(w, "run_agent", lauf_1)
    assert w.main() == 0
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    notiz = state[w.NOTIZ + "issue7"]
    assert notiz.startswith("Previous attempt failed: agent exited with code 1.") and FAILED in notiz
    assert len(gh.kommentare) == 1 and gh.kommentare[0][0] == "7" and FAILED in gh.kommentare[0][1]
    assert "----- Previous attempt -----" not in prompts[0]

    monkeypatch.setattr(w, "run_agent", lambda prompt: prompts.append(prompt) or 0)
    assert w.main() == 0
    assert w.notiz_block(notiz) in prompts[1]
    assert prompts[1].index(w.notiz_block(notiz)) < prompts[1].index(w.RUN_RULES)
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert w.NOTIZ + "issue7" not in state
    assert len(gh.kommentare) == 1
