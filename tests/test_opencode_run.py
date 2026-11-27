"""Tests für `sandbox/opencode_run.py` (Wegwerf-Klon für OpenCode-Läufe).

Ohne Netzwerk und ohne echtes Repository: Die Git-Aufrufe kommen als `run` herein, das
OpenCode-JSONL liegt als Datei in `tmp_path`. Damit prüfen die Tests die Befehlsfolge
(Klon ohne Hardlinks, Branch auf `origin/main`, Patch aus dem Index), die Token-Summe und
den Abbruch bei unbekanntem Repo.
"""

import json
import subprocess

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

    text = oc.ref_snapshot("/config/projects/home-infra", run=fake_run)
    assert [cmd[3] for cmd in aufrufe] == ["for-each-ref", "worktree", "stash"]
    assert "refs/heads/main abc123" in text
    assert "worktree /config/projects/home-infra" in text


def test_ref_snapshot_meldet_git_fehler_im_text():
    def fake_run(cmd):
        raise FileNotFoundError("git fehlt")

    text = oc.ref_snapshot("/tmp/repo", run=fake_run)
    assert "Fehler: git fehlt" in text


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


def test_clone_local_bricht_ab_wenn_der_klon_scheitert():
    aufrufe = []

    def fake_run(cmd):
        aufrufe.append(list(cmd))
        return _fehler(cmd)

    assert oc.clone_local("/a", "/b", "x", run=fake_run) is False
    assert len(aufrufe) == 1        # kein checkout auf einem halben Klon


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


# ---- main ----

def test_main_bricht_bei_unbekanntem_repo_ab(tmp_path):
    auftrag = tmp_path / "auftrag.md"
    auftrag.write_text("egal", encoding="utf-8")

    code = oc.main(["--repo", "gibt-es-nicht", "--branch", "docs/x",
                    "--prompt-file", str(auftrag), "--projects-dir", str(tmp_path)])
    assert code == 2
