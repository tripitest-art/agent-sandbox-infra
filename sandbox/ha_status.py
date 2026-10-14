#!/usr/bin/env python3
"""Workflow-Status des Agenten als JSON an einen Home-Assistant-Webhook schicken (Teil 1 von 2).

Sammelt den Stand des Agenten-Workflows je Projekt aus der Projektliste:
offene Issues mit Label `worker-task` nach Zustandslabel (`bereit`, `in-arbeit`,
`blockiert`, `entwurf`), offene PRs (offen/rot/laufend) sowie die PRs mit Label
`braucht-stephan`, an denen Stephan arbeiten muss. Dazu die Tagesbudgets der LiteLLM-Keys
(`spend`, `max_budget`, Prozent). Alles geht als JSON an `HA_STATUS_URL`.

Aufruf: `python sandbox/ha_status.py [--print]`. Mit `--print` wird der Status nur auf
stdout ausgegeben; sonst per POST an den Webhook aus `/etc/ha-status.env` (Modus 600).
Weder die Webhook-URL noch die Keys erscheinen je in einer Ausgabe (Regel 5: das Repo ist
öffentlich). Timer, `install.sh` und die HA-Sensoren folgen in Teil 2.

Ablauf je Projekt: `gh issue list`/`gh pr list` (nur lesend), Fehler werden je Projekt
abgefangen und als Name der Exception-Klasse gemeldet — nie der Meldungstext, er könnte
Zugangsdaten enthalten. Nur Standardbibliothek und `projects.py` liegen daneben.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # projects.py liegt daneben
import projects  # noqa: E402

STATES = ("bereit", "in-arbeit", "blockiert", "entwurf")
WAITING_LABEL = "braucht-stephan"
# Conclusions, die einen PR als rot zählen (GitHub statusCheckRollup).
RED_CONCLUSIONS = frozenset({"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"})
BUDGET_KEYS = (("LITELLM_WORKER_KEY", "worker"),
               ("LITELLM_REVIEW_KEY", "reviewer"),
               ("LITELLM_PLANER_KEY", "planer"))
DEFAULT_LITELLM = "http://192.168.0.211:4000"


def _labels(obj: dict) -> set[str]:
    """Labelnamen eines Issues/PRs aus dem `gh --json`-Format (Labels als [{"name": …}])."""
    return {lab.get("name") for lab in obj.get("labels") or [] if isinstance(lab, dict)}


def _pr_state(pr: dict) -> str:
    """„rot“, „laufend“ oder „gruen“ aus statusCheckRollup. Fehlt/leer: weder rot noch laufend."""
    running = False
    for check in pr.get("statusCheckRollup") or []:
        if check.get("conclusion") in RED_CONCLUSIONS:
            return "rot"
        if check.get("status") != "COMPLETED":
            running = True
    return "laufend" if running else "gruen"


def summarize_project(p: projects.Project, issues: list[dict], prs: list[dict]) -> dict:
    """Zustand eines Projekts: Issues je Zustandslabel, PR-Zahlen und wartende PRs."""
    counts = {state: 0 for state in STATES}
    for issue in issues:
        for state in STATES:
            if state in _labels(issue):
                counts[state] += 1
    rot = laufend = 0
    waiting: list[dict] = []
    for pr in prs:
        state = _pr_state(pr)
        if state == "rot":
            rot += 1
        elif state == "laufend":
            laufend += 1
        if WAITING_LABEL in _labels(pr):
            waiting.append({"number": pr["number"], "title": pr["title"], "url": pr["url"]})
    waiting.sort(key=lambda e: e["number"])
    return {
        "name": p.name,
        "repo": p.repo,
        "issues": counts,
        "prs": {"offen": len(prs), "rot": rot, "laufend": laufend, "wartet_auf_stephan": waiting},
    }


def summarize_budget(info: dict | None) -> dict:
    """LiteLLM-Budget eines Keys: spend, max_budget und Prozent. None = nicht erreichbar."""
    if info is None:
        return {"fehler": "nicht erreichbar"}
    spend = info.get("spend") or 0.0
    max_budget = info.get("max_budget")
    prozent = round(spend / max_budget * 100) if max_budget else None
    return {"spend": round(spend, 2), "max": max_budget, "prozent": prozent}


def build_status(items: list[projects.Project], fetch: Callable[[str, str], list[dict]],
                 budgets: dict[str, dict | None], now: datetime) -> dict:
    """Gesamtstatus: je Projekt ein summarize_project, die Summe der wartenden PRs und die Budgets.

    Wirft `fetch` für ein Projekt eine Exception, bekommt nur dieses Projekt einen
    `fehler`-Eintrag (Name der Klasse, nie der Meldungstext); die anderen laufen weiter.
    """
    projekte: list[dict] = []
    braucht_stephan = 0
    for p in items:
        try:
            issues = fetch("issues", p.repo)
            prs = fetch("prs", p.repo)
        except Exception as exc:   # noqa: BLE001 — ein kaputtes Projekt darf die anderen nicht stoppen
            projekte.append({"name": p.name, "repo": p.repo, "fehler": type(exc).__name__})
            continue
        summary = summarize_project(p, issues, prs)
        projekte.append(summary)
        braucht_stephan += len(summary["prs"]["wartet_auf_stephan"])
    return {
        "zeit": now.isoformat(timespec="seconds"),
        "projekte": projekte,
        "braucht_stephan": braucht_stephan,
        "budgets": {alias: summarize_budget(info) for alias, info in budgets.items()},
    }


def post_status(url: str, payload: dict, opener=urllib.request.urlopen, timeout: float = 10) -> bool:
    """payload als JSON-POST an url. True bei Status 2xx, sonst False. Gibt die URL nie aus."""
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = opener(request, timeout=timeout)
        try:
            status = getattr(resp, "status", None)
            return status is not None and 200 <= status < 300
        finally:
            resp.close()
    except Exception:   # noqa: BLE001 — Netzfehler und fremde Antworten zählen nur als „nicht gesendet“
        return False


def _gh_fetch(kind: str, repo: str) -> list[dict]:
    """Offene Issues (Label worker-task) bzw. PRs eines Repos über `gh` — nur lesend."""
    if kind == "issues":
        args = ["issue", "list", "--repo", repo, "--state", "open", "--label", "worker-task",
                "--json", "number,title,labels", "--limit", "200"]
    else:
        args = ["pr", "list", "--repo", repo, "--state", "open",
                "--json", "number,title,url,isDraft,labels,statusCheckRollup", "--limit", "100"]
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    text = out.stdout.strip()
    return json.loads(text) if text else []


def _default_key_info(key: str, base: str) -> dict | None:
    """`GET {base}/key/info` mit dem Key; das Feld `info` oder None bei jedem Fehler."""
    request = urllib.request.Request(f"{base.rstrip('/')}/key/info",
                                     headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:   # noqa: BLE001 — nicht erreichbar/kein JSON zählt als None
        return None
    info = data.get("info")
    return info if isinstance(info, dict) else None


def main(argv: list[str] | None = None, env: dict | None = None, *, fetch=None, key_info=None,
         opener=None, now: datetime | None = None) -> int:
    """`python sandbox/ha_status.py [--print]`. Rückgabe 0 (gesendet/gedruckt), 1 (Senden
    fehlgeschlagen) oder 2 (HA_STATUS_URL fehlt). URL und Keys werden nie ausgegeben."""
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    fetch = fetch or _gh_fetch
    key_info = key_info or (lambda key: _default_key_info(key, env.get("LITELLM") or DEFAULT_LITELLM))

    url = env.get("HA_STATUS_URL")
    if "--print" not in argv and not url:
        print("HA_STATUS_URL fehlt (/etc/ha-status.env)", file=sys.stderr)
        return 2

    items = projects.load_projects(env=env)
    budgets: dict[str, dict | None] = {}
    for var, alias in BUDGET_KEYS:
        key = env.get(var)
        if key:
            budgets[alias] = key_info(key)

    status = build_status(items, fetch, budgets, now)

    if "--print" in argv:
        print(json.dumps(status, ensure_ascii=False, indent=1))
        return 0
    if not post_status(url, status, opener=opener or urllib.request.urlopen):
        print("ha_status: Senden fehlgeschlagen", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
