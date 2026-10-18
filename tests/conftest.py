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
