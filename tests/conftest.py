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
import json  # noqa: E402
import tempfile  # noqa: E402

_projekte = os.path.join(tempfile.mkdtemp(prefix="sandbox-tests-"), "agent-projects.json")
with open(_projekte, "w", encoding="utf-8") as _fh:
    json.dump({"projects": [{"name": "sc-digger", "repo": "tripitest-art/sc-digger",
                             "workdir": "/kein/klon/sc-digger"}]}, _fh)
os.environ["AGENT_PROJECTS"] = _projekte
