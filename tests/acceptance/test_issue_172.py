import importlib.util
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SANDBOX = _ROOT / "sandbox"


def _load(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


w = _load("worker_tick_rahmen2", _SANDBOX / "worker_tick.py")
VORLAGE = (_ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
SUMMARY = "Fügt X hinzu.\n\nWeil Y."


def test_pr_text_ist_vollstaendig_und_stimmt_mit_der_vorlage_ueberein(monkeypatch):
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", VORLAGE)
    body = w.build_pr_body(VORLAGE, 7, "Gemini Flash", SUMMARY)
    assert w.pr_text_problems(body, 7) == []
    assert "Closes #7\n\n" + SUMMARY in body
    assert "Closes #\n" not in body
    assert "Worker: Gemini Flash" in body


def test_pr_text_haket_nur_ab_was_der_takt_selbst_pruefen_kann(monkeypatch):
    body = w.build_pr_body(VORLAGE, 7, "Gemini Flash", SUMMARY)
    zeilen = body.splitlines()
    assert any(z.startswith("- [x] `python -m pytest -q` grün") for z in zeilen)
    assert any(z.startswith("- [x] Akzeptanztests aus dem Issue zeichengenau") for z in zeilen)
    assert any(z.startswith("- [ ] Neue Logik") for z in zeilen)
    assert any(z.startswith("- [ ] Keine Zugangsdaten") for z in zeilen)
    abschnitt = w.extract_section(body, "## Offene Punkte / Abweichungen vom Issue")
    assert "keine" in abschnitt.split("-->")[-1]


def test_fehlende_stellen_der_vorlage_werden_angehaengt_statt_zu_werfen():
    body = w.build_pr_body("## Irgendwas\n\ntext\n", 9, "Qwen", "Zusammenfassung")
    assert "Closes #9" in body and "Zusammenfassung" in body
    assert "Worker: Qwen" in body and "## Irgendwas" in body


class Git:
    def __init__(self, anzahl="3", push_rc=0, nachrichten=("Akzeptanztests aus #7", "Baut X\n\nWeil Y", "Test dazu")):
        self.anzahl, self.push_rc, self.nachrichten, self.calls = anzahl, push_rc, nachrichten, []

    def __call__(self, cmd, **kw):
        self.calls.append((list(cmd), kw))
        out, rc = "", 0
        if list(cmd[:3]) == ["git", "rev-list", "--count"]:
            out = self.anzahl + "\n"
        elif cmd[1] == "push":
            rc = self.push_rc
            out = "geheime Ausgabe des Servers"
        elif cmd[1] == "log":
            out = "".join(m + "\n\x00" for m in self.nachrichten)
        return subprocess.CompletedProcess(cmd, rc, out, "")

    @property
    def cmds(self):
        return [c for c, _ in self.calls]


class GH:
    def __init__(self, offen=()):
        self.offen, self.calls = list(offen), []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return self.offen
        if args[:2] == ("issue", "view"):
            return {"title": "Titel des Issues"}
        if args[:2] == ("pr", "create"):
            return "https://github.com/tripitest-art/agent-sandbox-infra/pull/99"
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    @property
    def created(self):
        return [a for a in self.calls if a[:2] == ("pr", "create")]


def _setup(monkeypatch, tmp_path, gh, git):
    monkeypatch.setattr(w, "WORKDIR", str(tmp_path))
    monkeypatch.setattr(w, "_TEMPLATE_CACHE", VORLAGE)
    monkeypatch.setattr(w, "gh", gh)
    monkeypatch.setattr(w.subprocess, "run", git)


def test_offener_pr_bleibt_unangetastet(monkeypatch, tmp_path):
    gh, git = GH(offen=[{"number": 5}]), Git()
    _setup(monkeypatch, tmp_path, gh, git)
    assert w.open_pr(7) is True
    assert git.calls == [] and gh.created == []
    assert gh.calls[0] == ("pr", "list", "--state", "open", "--head", "feature/issue-7", "--json", "number")


def test_ohne_aenderung_ueber_den_akzeptanztest_hinaus_kein_pr(monkeypatch, tmp_path, capsys):
    for anzahl in ("1", "0"):
        gh, git = GH(), Git(anzahl=anzahl)
        _setup(monkeypatch, tmp_path, gh, git)
        assert w.open_pr(7) is False
        assert git.cmds == [["git", "rev-list", "--count", "origin/main..HEAD"]]
        assert gh.created == []
    assert "issue7: keine Änderungen über den Akzeptanztest hinaus, kein PR." in capsys.readouterr().out


def test_abgelehnter_push_meldet_und_legt_keinen_pr_an(monkeypatch, tmp_path, capsys):
    gh, git = GH(), Git(push_rc=1)
    _setup(monkeypatch, tmp_path, gh, git)
    assert w.open_pr(7) is False
    assert git.cmds[-1] == ["git", "push", "-u", "origin", "feature/issue-7"]
    assert gh.created == []
    out = capsys.readouterr().out
    zeilen = [z for z in out.splitlines() if "⚠ Stephan:" in z]
    assert len(zeilen) == 1 and "issue7" in zeilen[0] and "Push abgelehnt" in zeilen[0]
    assert "geheime Ausgabe" not in out


def test_pr_wird_mit_text_aus_den_commit_nachrichten_angelegt(monkeypatch, tmp_path):
    gh, git = GH(), Git()
    _setup(monkeypatch, tmp_path, gh, git)
    assert w.open_pr(7) is True
    assert git.cmds == [["git", "rev-list", "--count", "origin/main..HEAD"],
                        ["git", "push", "-u", "origin", "feature/issue-7"],
                        ["git", "log", "--reverse", "--format=%B%x00", "origin/main..HEAD"]]
    assert all(kw.get("cwd") == str(tmp_path) for _, kw in git.calls)
    (aufruf,) = gh.created
    assert aufruf[:6] == ("pr", "create", "--title", "Titel des Issues", "--body", aufruf[5])
    assert aufruf[6:] == ("--head", "feature/issue-7")
    body = aufruf[5]
    assert "Baut X\n\nWeil Y\n\nTest dazu" in body
    assert "Akzeptanztests aus #7" not in body
    assert "Closes #7" in body and f"Worker: {w.WORKER}" in body
    assert w.pr_text_problems(body, 7) == []


class FakeGH:
    def __init__(self, issues):
        self.issues = issues

    def __call__(self, *args):
        if args[:2] == ("issue", "list"):
            return [] if "in-arbeit" in args else self.issues
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")


def test_auftragstext_uebergibt_push_und_pr_an_den_takt(monkeypatch):
    monkeypatch.setattr(w, "HOTSPOT", "")
    monkeypatch.setattr(w, "TEST_CMD", "python -m pytest -q")
    monkeypatch.setattr(w, "template_rules", lambda: "")
    body = "### Betroffene Dateien\n\n- sandbox/foo.py\n\n### Akzeptanztests\n\n```python\ndef test_a():\n    pass\n```\n"
    issue = {"number": 102, "title": "Titel", "author": {"login": "tripitest-art"}, "labels": [], "body": body}
    monkeypatch.setattr(w, "gh", FakeGH([issue]))
    key, task, pr_no, sha = w.issue_job()
    assert "Push und Pull Request legt der Takt an; du pushst nicht und legst keinen PR an." in task
    assert "git push -u origin feature/issue-102" not in task
    assert "gh pr create" not in task
    assert "--body-file .git/pr-body.md" not in task
    assert w.PUSH_RULE in task
