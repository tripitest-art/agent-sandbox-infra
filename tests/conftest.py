"""Gemeinsame Test-Einstellungen.

Die Tests dürfen nie vom Server abhängen, auf dem sie laufen. Der Auto-Deploy führt `pytest` im
Infra-Klon auf dem Server aus; dort gibt es den echten Worker-Klon (`WORKDIR`). Ältere Tests faken
`prepare_workdir`, setzen `WORKDIR` aber nicht und verlassen sich darauf, dass es das Verzeichnis
nicht gibt (`worker_tick.main`: `os.path.isdir(WORKDIR) and not prepare_issue_branch(n)`). Auf dem
Server lief dann `prepare_issue_branch` wirklich los und der Auto-Deploy rollte `56f7582` zurück.
"""
import os

# Vor dem Import von `worker_tick` (liest WORKDIR beim Import), auch wenn die Umgebung etwas setzt.
os.environ["WORKDIR"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_kein_workdir")

# `/etc/agent-settings.json` gewinnt vor Umgebung und Code-Vorgabe (agent_settings.py). Auf dem Server
# steht dort z. B. `LITELLM_MODEL=coder-nvidia`; `worker_tick` liest es beim Import, und die Tests zum
# Modell-Ausweichen und zu `agent_cmd` erwarten die Vorgabe `coder`. Ein Pfad ohne Datei = Vorgaben.
os.environ["AGENT_SETTINGS"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_keine_einstellungen.json")

# `use_project()` setzt WORKDIR in `worker_tick.main` aus der Projektliste neu und überschreibt damit die
# Umgebungsvariable oben. Auf dem Server (zwei Projekte in `/etc/agent-projects.json`, ohne Datei gilt
# `/root/sc-digger`) zeigt das auf einen echten Klon; `test_issue_38/39` liefen dort in
# `prepare_issue_branch` und der Auto-Deploy rollte `609fb8c` zurück. Darum eine eigene Liste mit
# Klonpfaden, die es nirgends gibt.
import atexit  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

_projekte = os.path.join(tempfile.mkdtemp(prefix="sandbox-tests-"), "agent-projects.json")
with open(_projekte, "w", encoding="utf-8") as _fh:
    json.dump({"projects": [{"name": "sc-digger", "repo": "tripitest-art/sc-digger",
                             "workdir": "/kein/klon/sc-digger"}]}, _fh)
os.environ["AGENT_PROJECTS"] = _projekte

# Slot-Status, Audit-Log, Zustandsdateien und Review-Entwürfe haben Vorgaben unter `/run` und `/root`. Auf dem
# Server führen Auto-Deploy und Pre-Flight `pytest` als root aus; jeder Test, der `main()`, `slot_status()` oder
# `audit()` ohne eigenes `monkeypatch` aufruft, schrieb dort in die echten Dateien (23.10.2026: Slot 1 stand auf
# „idle“, während `w1` arbeitete, und `/root/audit.jsonl` enthielt 172 Fake-Läufe von „Issue 7“). Darum ein
# eigenes Verzeichnis je Testlauf, bevor die Skripte ihre Pfade beim Import lesen.
_lauf = tempfile.mkdtemp(prefix="sandbox-run-")
os.environ["SLOT_DIR"] = os.path.join(_lauf, "slots")
os.makedirs(os.environ["SLOT_DIR"])
os.environ["AUDIT_LOG"] = os.path.join(_lauf, "audit.jsonl")
os.environ["STATE"] = os.path.join(_lauf, "worker-state.json")
os.environ["REVIEW_STATE"] = os.path.join(_lauf, "review-state.json")
os.environ["REVIEW_DRAFTS"] = os.path.join(_lauf, "reviews")

# Ersatz für `gh` (Goldene Regel 3: Tests laufen offline, ohne `gh`). Ohne ihn rufen die `main()`-Tests, die `w.gh`
# nicht durchgehend faken, das echte `gh` auf: Auf einem Rechner mit angemeldetem `gh` setzt `claim_issue()` dann
# wirklich Labels an GitHub (sc-digger #7 und #42, 23.10.2026), und jeder Lauf wartet auf das Netz (71 s statt 31 s).
# Der Ersatz verhält sich wie ein nicht angemeldetes `gh` in GitHub Actions; `worker_tick.gh()` liefert dafür None.
# Muss vor dem Import der Takt-Skripte stehen: Sie bauen `ENV` mit dem PATH von hier.
_gh_bin = tempfile.mkdtemp(prefix="sandbox-bin-")
atexit.register(shutil.rmtree, _gh_bin, ignore_errors=True)   # kein Verzeichnis je Testlauf liegen lassen
_gh_meldung = "gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN environment variable."
_gh_pfad = os.path.join(_gh_bin, "gh")
with open(_gh_pfad, "w", encoding="utf-8") as _fh:
    _fh.write(f'#!/bin/sh\necho "{_gh_meldung}" >&2\nexit 4\n')
os.chmod(_gh_pfad, 0o755)
if os.name == "nt":
    with open(_gh_pfad + ".cmd", "w", encoding="utf-8") as _fh:
        _fh.write(f"@echo {_gh_meldung} 1>&2\r\n@exit /b 4\r\n")
os.environ["SANDBOX_TEST_BIN"] = _gh_bin
os.environ["PATH"] = _gh_bin + os.pathsep + os.environ.get("PATH", "")
