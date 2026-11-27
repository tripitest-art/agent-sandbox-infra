"""Tests für `sandbox/opencode_run.py` (Wegwerf-Klon für OpenCode-Läufe).

Ohne Netzwerk und ohne echtes Repository: Die Git-Aufrufe kommen als `run` herein, das
OpenCode-JSONL liegt als Datei in `tmp_path`. Damit prüfen die Tests die Befehlsfolge
(Klon ohne Hardlinks, Branch auf `origin/main`, Patch aus dem Index), die Token-Summe und
den Abbruch bei unbekanntem Repo.
"""

import json
import subprocess
from pathlib import Path

from sandbox import opencode_run as oc


def _ok(cmd, stdout=""):
    return subprocess.CompletedProcess(cmd, 0, stdout, "")


def _fehler(cmd, rc=128):
    return subprocess.CompletedProcess(cmd, rc, "", "fatal")


def _ereignis(art, teil):
    return json.dumps({"type": art, "part": teil})


def test_auftrag_bekommt_den_hinweis_auf_das_arbeitsverzeichnis():
    text = oc.auftrag_mit_hinweis("Tu etwas.  \n\n", "/config/projects/home-infra")
    assert text.startswith("Tu etwas.")
    assert "Arbeitsverzeichnis" in text
    assert "/config/projects/home-infra" in text
    assert text.endswith("außerhalb des Klons.")


# ---- ref_snapshot / ref_diff ----

def test_ref_snapshot_fragt_refs_worktrees_und_stashes():
    aufrufe = []
    antworten = {
        "for-each-ref": "refs/heads/main abc123\n",
        "worktree": "worktree /config/projects/home-infra\n",
        "stash": "",
    }

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        return _ok(cmd, stdout=antworten[cmd[3]])

    text, fehler = oc.ref_snapshot("/config/projects/home-infra", run=fake_run)
    assert [cmd[3] for cmd in aufrufe] == ["for-each-ref", "worktree", "stash"]
    assert "refs/heads/main abc123" in text
    assert "worktree /config/projects/home-infra" in text
    assert fehler == ""


def test_ref_snapshot_meldet_git_fehler_im_text_und_als_fehler():
    def fake_run(cmd):
        raise FileNotFoundError("git fehlt")

    text, fehler = oc.ref_snapshot("/tmp/repo", run=fake_run)
    assert "Fehler: git fehlt" in text
    assert "git fehlt" in fehler


def test_ref_snapshot_meldet_fehlercode_statt_leerem_text():
    # Wie auf CT 106 gesehen: `git for-each-ref` als fremder User -> rc=128, kein stdout.
    def fake_run(cmd):
        return _fehler(cmd)

    text, fehler = oc.ref_snapshot("/config/projects/home-infra", run=fake_run)
    assert text.strip()      # der Text bleibt als Vergleichsgrundlage erhalten
    assert "for-each-ref" in fehler and "worktree" in fehler
    assert fehler.count("rc=128") == 3


def test_ref_diff_bleibt_leer_bei_gleichem_stand():
    assert oc.ref_diff("a\nb\n", "a\nb\n") == ""


def test_ref_diff_zeigt_neue_ref():
    diff = oc.ref_diff("refs/heads/main a\n", "refs/heads/main a\nrefs/heads/zz-reftest b\n")
    assert diff.startswith("--- vorher")
    assert "refs/heads/zz-reftest b" in diff


# ---- clone_local ----

def test_clone_local_nutzt_no_hardlinks_und_legt_branch_an():
    aufrufe = []

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        return _ok(cmd)

    assert oc.clone_local("/config/projects/home-infra",
                          "/config/work/opencode-runs/lauf", "docs/probe",
                          run=fake_run) is True
    assert aufrufe[0] == ["git", "clone", "--local", "--no-hardlinks",
                          "/config/projects/home-infra", "/config/work/opencode-runs/lauf"]
    assert aufrufe[1] == ["git", "-C", "/config/work/opencode-runs/lauf",
                          "checkout", "-b", "docs/probe", "origin/main"]
    # Kein Remote im Klon: sonst ist origin eine Push-URL aufs Haupt-Repo.
    assert aufrufe[2] == ["git", "-C", "/config/work/opencode-runs/lauf",
                          "remote", "remove", "origin"]


def test_clone_local_bricht_ab_wenn_das_remote_bleibt():
    def fake_run(cmd):
        if "remote" in cmd:
            return _fehler(cmd)
        return _ok(cmd)

    assert oc.clone_local("/a", "/b", "x", run=fake_run) is False


def test_clone_local_bricht_ab_wenn_der_klon_scheitert():
    aufrufe = []

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        return _fehler(cmd)

    assert oc.clone_local("/a", "/b", "x", run=fake_run) is False
    assert len(aufrufe) == 1        # kein checkout auf einem halben Klon


# ---- fetch_main ----

def test_fetch_main_ist_erfolgreich_bei_null():
    assert oc.fetch_main("/config/projects/home-infra", run=lambda cmd: _ok(cmd)) is True


def test_fetch_main_meldet_fehler_bei_fehlercode():
    assert oc.fetch_main("/config/projects/home-infra", run=lambda cmd: _fehler(cmd)) is False


def test_fetch_main_faengt_subprozess_ausnahmen():
    def fake_run(cmd):
        raise OSError("git fehlt")

    assert oc.fetch_main("/config/projects/home-infra", run=fake_run) is False


# ---- export_patch ----

def test_export_patch_staged_und_schreibt_binary_diff(tmp_path):
    aufrufe = []

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        if "--stat" in cmd:
            return _ok(cmd, " docs/x.md | 2 ++\n")
        if "--binary" in cmd:
            return _ok(cmd, "diff --binary-inhalt")
        return _ok(cmd)

    ziel = tmp_path / "lauf.patch"
    stat = oc.export_patch("/config/work/klon", ziel, run=fake_run)
    assert "docs/x.md | 2 ++" in stat
    assert ziel.read_text(encoding="utf-8") == "diff --binary-inhalt"
    assert aufrufe[0][3:] == ["add", "-A"]
    assert aufrufe[1][-2:] == ["--cached", "--stat"]
    assert aufrufe[2][-2:] == ["--cached", "--binary"]


def test_export_patch_bleibt_leer_wenn_stat_scheitert(tmp_path):
    def fake_run(cmd):
        if "--stat" in cmd:
            return _fehler(cmd)
        return _ok(cmd, "diff --binary-inhalt")

    ziel = tmp_path / "lauf.patch"
    assert oc.export_patch("/config/work/klon", ziel, run=fake_run) == ""
    assert not ziel.exists()          # kein halber Patch auf der Platte


def test_export_patch_bleibt_leer_wenn_das_schreiben_scheitert(tmp_path):
    def fake_run(cmd):
        return _ok(cmd, "diff --binary-inhalt" if "--binary" in cmd else "docs/x.md | 1 +")

    # Elternverzeichnis fehlt -> write_text wirft FileNotFoundError (OSError).
    ziel = tmp_path / "kein-verzeichnis" / "lauf.patch"
    assert oc.export_patch("/config/work/klon", ziel, run=fake_run) == ""


# ---- summarize_log ----

def test_summarize_log_summiert_tokens_und_zaehlt_sperren(tmp_path):
    log = tmp_path / "lauf.jsonl"
    log.write_text("\n".join([
        _ereignis("step_start", {"type": "step-start"}),
        _ereignis("step_finish", {"tokens": {"input": 100, "output": 10, "reasoning": 5,
                                             "cache": {"read": 40, "write": 2}}}),
        _ereignis("step_finish", {"tokens": {"input": 50, "output": 7, "reasoning": 0,
                                             "cache": {"read": 0, "write": 0}}}),
        _ereignis("tool_use", {"state": {"status": "error",
                                         "error": "Permission denied: shell"}}),
        _ereignis("tool_use", {"state": {"status": "error", "error": "read fehlgeschlagen"}}),
        _ereignis("tool_use", {"state": {"status": "completed"}}),
        "{\"kaputte letzte Zeile",
    ]), encoding="utf-8")

    summe = oc.summarize_log(log)
    assert summe["input"] == 150
    assert summe["output"] == 17
    assert summe["reasoning"] == 5
    assert summe["cache_read"] == 40
    assert summe["cache_write"] == 2
    assert summe["schritte"] == 2
    assert summe["tool_fehler"] == 2
    assert summe["gesperrt"] == 1


def test_summarize_log_fehlende_datei_liefert_nullen(tmp_path):
    summe = oc.summarize_log(tmp_path / "gibt-es-nicht.jsonl")
    assert summe["input"] == 0 and summe["schritte"] == 0 and summe["gesperrt"] == 0


# ---- limit_hinweis ----

def test_limit_hinweis_nennt_reset_zeit_und_alternatives_modell(tmp_path):
    log = tmp_path / "lauf.jsonl"
    log.write_text(json.dumps({"type": "error", "timestamp": 1_000_000_000_000, "error": {
        "type": "provider.free-limit", "status": 429, "retryAfterMs": 3_600_000}}),
        encoding="utf-8")

    text = oc.limit_hinweis(log)
    assert "Gratis-Limit erreicht (HTTP 429" in text
    assert "Reset um 09.09. 04:46 (Europe/Berlin)" in text
    assert "opencode-go/hy4-flash-free" in text


def test_limit_hinweis_ohne_429_bleibt_leer(tmp_path):
    log = tmp_path / "lauf.jsonl"
    log.write_text("\n".join([
        _ereignis("step_finish", {"tokens": {"input": 1}}),
        json.dumps({"type": "error", "error": {"status": 500}}),
    ]), encoding="utf-8")

    assert oc.limit_hinweis(log) == ""
    assert oc.limit_hinweis(tmp_path / "gibt-es-nicht.jsonl") == ""


def test_limit_hinweis_ignoriert_fehler_als_string(tmp_path):
    """`error` ist nicht immer ein Dict — ein String darf keinen AttributeError auslösen."""
    log = tmp_path / "lauf.jsonl"
    log.write_text(json.dumps({"type": "error", "error": "kaputt"}), encoding="utf-8")

    assert oc.limit_hinweis(log) == ""


# ---- sperre_fehlt ----

def test_sperre_fehlt_erkennt_die_installierte_sperre(tmp_path):
    hook = tmp_path / ".git" / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True)
    hook.write_text(f"#!/bin/sh\necho \"{oc.SPERRE_MARKE}\" >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    assert oc.sperre_fehlt(tmp_path) is False


def test_sperre_fehlt_bei_fehlender_datei_und_bei_fremdem_hook(tmp_path):
    assert oc.sperre_fehlt(tmp_path) is True
    hook = tmp_path / ".git" / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)

    assert oc.sperre_fehlt(tmp_path) is True


def test_sperre_fehlt_bei_nicht_ausfuehrbarem_hook(tmp_path):
    """Ohne X-Bit ignoriert Git den Hook — Inhalt allein ist keine Sperre."""
    hook = tmp_path / ".git" / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True)
    hook.write_text(f"#!/bin/sh\necho \"{oc.SPERRE_MARKE}\" >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o644)

    assert oc.sperre_fehlt(tmp_path) is True


# ---- main ----

def test_main_bricht_bei_unbekanntem_repo_ab(tmp_path):
    auftrag = tmp_path / "auftrag.md"
    auftrag.write_text("egal", encoding="utf-8")

    code = oc.main(["--repo", "gibt-es-nicht", "--branch", "docs/x",
                    "--prompt-file", str(auftrag), "--projects-dir", str(tmp_path)])
    assert code == 2


def test_main_bricht_ab_wenn_der_ref_schnappschuss_nicht_lesbar_ist(tmp_path, monkeypatch):
    """Leerer Schnappschuss darf nicht als „unverändert" durchgehen: Exit 2 statt 0."""
    (tmp_path / "home-infra" / ".git").mkdir(parents=True)
    auftrag = tmp_path / "auftrag.md"
    auftrag.write_text("egal", encoding="utf-8")
    monkeypatch.setattr(oc, "fetch_main", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(oc, "ref_snapshot", lambda *a, **k: ("", "for-each-ref: rc=128"))
    runs = tmp_path / "runs"

    code = oc.main(["--repo", "home-infra", "--branch", "docs/x",
                    "--prompt-file", str(auftrag), "--projects-dir", str(tmp_path),
                    "--runs-dir", str(runs)])

    assert code == 2
    assert not runs.exists()          # kein Klon und kein Modell-Call vor dem Abbruch


def test_main_meldet_unlesbare_prompt_datei_als_aufbaufehler(tmp_path, capsys):
    (tmp_path / "home-infra" / ".git").mkdir(parents=True)
    runs = tmp_path / "runs"

    code = oc.main(["--repo", "home-infra", "--branch", "docs/x",
                    "--prompt-file", str(tmp_path / "gibt-es-nicht.md"),
                    "--projects-dir", str(tmp_path), "--runs-dir", str(runs)])

    assert code == 2
    assert "nicht lesbar" in capsys.readouterr().err
    assert not runs.exists()


def _lauf_vorbereiten(tmp_path, monkeypatch, schnappschuesse=None, opencode=None,
                      patch_schreiben=True):
    """Repo, Auftrag und Stubs (git, Klon, Patch, OpenCode) für einen vollen `main()`-Lauf."""
    (tmp_path / "home-infra" / ".git").mkdir(parents=True)
    auftrag = tmp_path / "auftrag.md"
    auftrag.write_text("egal", encoding="utf-8")
    monkeypatch.setattr(oc, "fetch_main", lambda *a, **k: True)
    folge = iter(schnappschuesse or (("refs/heads/main a\n", "") for _ in range(2)))
    monkeypatch.setattr(oc, "ref_snapshot", lambda *a, **k: next(folge))

    def fake_clone(source, target, branch, run=None):
        Path(target).mkdir(parents=True)
        return True

    monkeypatch.setattr(oc, "clone_local", fake_clone)

    def fake_export(klon, patch, run=None):
        if patch_schreiben:
            Path(patch).write_text("", encoding="utf-8")
        return " docs/x.md | 1 +\n" if patch_schreiben else ""

    monkeypatch.setattr(oc, "export_patch", fake_export)

    def fake_opencode(cmd, **kwargs):
        if isinstance(opencode, Exception):
            raise opencode
        return subprocess.CompletedProcess(cmd, opencode or 0, "", "")

    monkeypatch.setattr(oc.subprocess, "run", fake_opencode)
    return ["--repo", "home-infra", "--branch", "docs/x", "--prompt-file", str(auftrag),
            "--projects-dir", str(tmp_path), "--runs-dir", str(tmp_path / "runs"),
            "--logs-dir", str(tmp_path / "logs"), "--opencode", "opencode-stub"]


def test_main_regulaerer_lauf_endet_mit_null(tmp_path, monkeypatch, capsys):
    code = oc.main(_lauf_vorbereiten(tmp_path, monkeypatch))
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "Klon:" in ausgabe and "Tokens:" in ausgabe
    assert "docs/x.md | 1 +" in ausgabe


def test_main_warnt_bei_veraenderten_haupt_repo_refs(tmp_path, monkeypatch, capsys):
    args = _lauf_vorbereiten(tmp_path, monkeypatch, schnappschuesse=(
        ("refs/heads/main a\n", ""),
        ("refs/heads/main a\nrefs/heads/zz-reftest b\n", "")))

    assert oc.main(args) == 1
    assert oc.WARNUNG in capsys.readouterr().out


def test_main_meldet_opencode_fehler_mit_drei(tmp_path, monkeypatch):
    assert oc.main(_lauf_vorbereiten(tmp_path, monkeypatch, opencode=1)) == 3


def test_main_meldet_zeitlimit_mit_drei(tmp_path, monkeypatch, capsys):
    args = _lauf_vorbereiten(
        tmp_path, monkeypatch, opencode=subprocess.TimeoutExpired("opencode", 900))

    assert oc.main(args) == 3
    assert "Zeitlimit" in capsys.readouterr().out


def test_main_meldet_nicht_geschriebenen_patch_mit_drei(tmp_path, monkeypatch, capsys):
    args = _lauf_vorbereiten(tmp_path, monkeypatch, patch_schreiben=False)

    assert oc.main(args) == 3
    assert "nicht geschrieben" in capsys.readouterr().err


def test_main_warnt_ohne_pre_receive_sperre_und_laeuft_weiter(tmp_path, monkeypatch, capsys):
    """Fehlender Deploy darf den Lauf nicht stoppen — sonst steht die ganze Kette."""
    args = _lauf_vorbereiten(tmp_path, monkeypatch)

    assert oc.main(args) == 0
    assert "WARNUNG: pre-receive-Sperre fehlt in home-infra" in capsys.readouterr().err


def test_main_schweigt_bei_installierter_sperre(tmp_path, monkeypatch, capsys):
    """Gegenprobe: liegt die ausführbare Sperre, darf keine Warnung kommen."""
    args = _lauf_vorbereiten(tmp_path, monkeypatch)
    hook = tmp_path / "home-infra" / ".git" / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True)
    hook.write_text(f"#!/bin/sh\necho \"{oc.SPERRE_MARKE}\" >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    assert oc.main(args) == 0
    assert "WARNUNG" not in capsys.readouterr().err

