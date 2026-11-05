import importlib.util
import subprocess
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick_ablage", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick_ablage"] = w
_spec.loader.exec_module(w)

ISSUE_BODY = ("### Ziel\n\nEtwas.\n\n### Betroffene Dateien\n\n- `sandbox/a.py`\n\n"
              "### Fertig, wenn\n\n- [ ] Tests gruen\n")


class FakeGH:
    def __init__(self, issues=()):
        self.issues = list(issues)
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        if args[:2] == ("issue", "view"):
            return {"labels": [{"name": "agent-qwen"}], "title": "Test Issue"}
        if args[:2] in (("issue", "edit"), ("issue", "comment")):
            return None
        if args[:2] == ("pr", "list"):
            return []
        if args[:2] == ("run", "list") or args[:1] == ("api",):
            return []
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def _rev_parse(stdout):
    def run(cmd, **kwargs):
        assert cmd == ["git", "rev-parse", "--git-common-dir"]
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run


def test_agent_ablage_ist_ein_ordner_im_arbeitsbaum(monkeypatch, tmp_path):
    assert w.AGENT_ABLAGE == ".agent"
    assert w.agent_ablage("frage.md", str(tmp_path)) == str(tmp_path / ".agent" / "frage.md")
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path / "w1"))
    assert w.agent_ablage("pr-body.md") == str(tmp_path / "w1" / ".agent" / "pr-body.md")


def test_ablage_vorbereiten_raeumt_auf_und_traegt_exclude_einmal_ein(monkeypatch, tmp_path):
    work = tmp_path / "w1"
    (work / ".agent").mkdir(parents=True)
    (work / ".agent" / "frage.md").write_text("alte Frage", encoding="utf-8")
    common = tmp_path / "basis" / ".git"
    (common / "info").mkdir(parents=True)
    (common / "info" / "exclude").write_text("# vorhanden\n*.tmp\n", encoding="utf-8")
    monkeypatch.setattr(w.subprocess, "run", _rev_parse(f"{common}\n"))
    assert w.ablage_vorbereiten(str(work)) is True
    assert not (work / ".agent").exists()
    text = (common / "info" / "exclude").read_text(encoding="utf-8")
    assert text.startswith("# vorhanden\n*.tmp\n")
    assert text.splitlines().count("/.agent/") == 1
    assert w.ablage_vorbereiten(str(work)) is True
    text = (common / "info" / "exclude").read_text(encoding="utf-8")
    assert text.splitlines().count("/.agent/") == 1


def test_ablage_vorbereiten_relativer_git_pfad_und_fehlendes_info(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(w.subprocess, "run", _rev_parse(".git\n"))
    assert w.ablage_vorbereiten(str(tmp_path)) is True
    text = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.agent/" in text.splitlines()


def test_ablage_vorbereiten_meldet_fehler_und_wirft_nie(monkeypatch, tmp_path):
    def kaputt(cmd, **kwargs):
        raise subprocess.CalledProcessError(128, cmd)
    zeilen = []
    monkeypatch.setattr(w.subprocess, "run", kaputt)
    monkeypatch.setattr(w, "log", zeilen.append)
    assert w.ablage_vorbereiten(str(tmp_path)) is False
    assert zeilen == ["Warnung: Ablage .agent nicht vorbereitet (CalledProcessError)."]


def test_rueckfrage_aus_der_ablage_wird_gepostet(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    (tmp_path / ".agent").mkdir()
    frage = tmp_path / ".agent" / "frage.md"
    frage.write_text("Der Akzeptanztest erwartet X, das Issue Y.", encoding="utf-8")
    fake = FakeGH()
    monkeypatch.setattr(w, "gh", fake)
    assert w.frage_des_agenten_posten(7) is True
    assert fake.calls == [("issue", "comment", "7", "--body-file", str(frage))]
    assert not frage.exists()


def test_laufregeln_nennen_kein_gh_als_werkzeug():
    assert "Use gh" not in w.RUN_RULES
    assert "as a comment on the issue" not in w.RUN_RULES
    assert "3. ESCALATION: Work without asking back." in w.RUN_RULES
    assert "`.agent/frage.md`" in w.RUN_RULES
    assert w.RUN_RULES.rstrip().endswith(
        "You have no GitHub access: never call gh. Push, pull request and comments are done by the tick.")
    text = w.ERROR_RULE.format(where="on issue #7")
    assert "`.agent/frage.md`" in text
    assert "on issue #7" in text and "NOT" in text and "comment" in text.lower()


def test_issue_auftrag_nennt_die_ablage(monkeypatch):
    kandidat = {"number": 42, "title": "Test Issue", "body": ISSUE_BODY,
                "author": {"login": w.OWNER}, "labels": [{"name": "bereit"}]}
    monkeypatch.setattr(w, "gh", FakeGH(issues=[kandidat]))
    key, task, _, _ = w.issue_job()
    assert key == "issue42"
    assert "write the reason to `.agent/frage.md` and stop;" in task
    assert ".git/frage.md" not in task
