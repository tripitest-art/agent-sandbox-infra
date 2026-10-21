"""GitHub-CLI (`gh`) als eigenständiges Modul.

Ein Aufruf, JSON wenn die Ausgabe JSON ist, sonst der Text: `gh issue edit` & Co. geben die
Issue-URL aus. Früher brach das Claiming ab, obwohl das Label schon gesetzt war (#52, #246,
#247 blieben auf `in-arbeit`).
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, Mapping

GH_TIMEOUT = 30.0              # Sekunden; ein hängender `gh`-Aufruf darf den Takt nicht sperren


def gh(
    *args: str,
    repo: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = GH_TIMEOUT,
) -> Any:
    """Führt `gh` aus und liefert die Ausgabe als JSON, Text oder None.

    - `repo` hängt `--repo <repo>` an, außer der Aufruf beginnt mit `api` (dort steht das
      Repository schon im Pfad, und `gh api` kennt die Option nicht).
    - None bei leerer Ausgabe und beim Token-Fehler (Exitcode 4):
      Ohne Token ist kein GitHub-Zugriff möglich, das ist ein Zustand und kein Absturz.
    - Geparstes JSON, wenn die Ausgabe valides JSON ist; sonst der getrimmte Rohtext.
    - Jeder andere Fehler (Rate-Limit, Netz, Berechtigung) wird als
      `subprocess.CalledProcessError` weitergereicht; `env` ersetzt die Umgebung des Aufrufs,
      `timeout` begrenzt die Laufzeit.
    """
    repo_args = [] if args and args[0] == "api" else (["--repo", repo] if repo else [])
    try:
        out = subprocess.run(
            ["gh", *args, *repo_args],
            capture_output=True,
            text=True,
            check=True,
            env=dict(env) if env is not None else None,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 4:
            return None
        raise
    text = out.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def gh_json(
    *args: str,
    repo: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = GH_TIMEOUT,
) -> Any:
    """Wie `gh()`, verlangt aber JSON (Objekt oder Liste) und wirft sonst ValueError.

    Für Aufrufer, die eine Liste oder ein Objekt erwarten: Eine URL als Antwort ist dann ein
    Fehler und soll sichtbar scheitern, nicht als Text weiterlaufen. Leere Ausgabe bleibt None.
    """
    res = gh(*args, repo=repo, env=env, timeout=timeout)
    if res is None or isinstance(res, (dict, list)):
        return res
    raise ValueError("gh lieferte kein JSON")
