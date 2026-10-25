"""merge-nach-freigabe: mergt einen PR, sobald die Reviewer-App ihn freigegeben hat.

Warum es das gibt: Der Reviewer läuft als GitHub App mit `contents: read` und kann
physisch nicht mergen. Der Merge passiert deshalb hier — aber nur unter Bedingungen, die
dieser Check selbst über die API nachliest, nicht unter denen, die ein Label behauptet.

Ausgelöst wird der Lauf durch das Label `review-freigabe`, das der Review-Takt setzt.
Gemergt wird nur, wenn **alle** Bedingungen gleichzeitig gelten (fail-closed: Unbekanntes
blockiert, es gibt keinen Weg, an dieser Liste vorbei zu mergen):

1. Das Label wurde vom Reviewer-Bot oder einem Konto aus `GUARD_TRUSTED_ACTORS` gesetzt.
2. Der PR ist kein Entwurf, und GitHub meldet ihn als mergebar (`mergeable == True`).
3. Die neueste Review des Bots auf dem **aktuellen** Head-SHA lautet `APPROVED`.
4. Keine offene Änderungsanforderung (`CHANGES_REQUESTED`) auf dem Head-SHA.
5. Alle Checks sind abgeschlossen und erfolgreich, darunter `acceptance-guard`.

Der Merge geht mit `sha` an die Merge-API: Hat sich der Head zwischen Prüfung und Merge
verschoben, lehnt GitHub ab, statt einen ungeprüften Stand zu mergen. Der PR-Code wird nie
ausgecheckt und nie ausgeführt; nur Standardbibliothek, damit der Job ohne pip auskommt.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

MERGE_LABEL = "review-freigabe"
REQUIRED_CHECK = "acceptance-guard"
SELF_CHECK = "merge-nach-freigabe"
TRUSTED_ACTORS_ENV = "GUARD_TRUSTED_ACTORS"
DEFAULT_BOT = "sandbox-agent-reviewer-app[bot]"
# "skipped"/"neutral" gelten als erfolgreich: ein nicht anwendbarer Check ist kein Fehler.
OK_CONCLUSIONS = {"success", "neutral", "skipped"}


@dataclass
class Decision:
    merge: bool
    reasons: list[str] = field(default_factory=list)


def _instant(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def trusted_actors() -> set[str]:
    """Konten aus `GUARD_TRUSTED_ACTORS` (Komma-Liste, kleingeschrieben). Leer = niemand."""
    return {p.strip().lower() for p in os.environ.get(TRUSTED_ACTORS_ENV, "").split(",") if p.strip()}


def latest_reviews(reviews: list[dict]) -> dict[str, dict]:
    """Je Login die zuletzt abgegebene Review.

    Ohne diese Reduktion würde eine alte Freigabe eine spätere Änderungsanforderung
    desselben Kontos überdecken.
    """
    newest: dict[str, dict] = {}
    for r in reviews:
        login = ((r.get("user") or {}).get("login") or "").lower()
        if not login:
            continue
        stamp = r.get("submitted_at") or ""
        if login not in newest or stamp >= (newest[login].get("submitted_at") or ""):
            newest[login] = r
    return newest


def decide(pr: dict, reviews: list[dict], checks: dict, label_actor: str | None,
           trusted: set[str], bot: str) -> Decision:
    """Reine Logik, damit sie ohne GitHub testbar ist.

    pr: pull_request aus dem Event (number, draft, mergeable, head.sha, head.ref)
    reviews: Einträge aus GET /pulls/{n}/reviews
    checks: {"state": …, "runs": [{"name", "status", "conclusion"}, …]}
    label_actor: Login, der `review-freigabe` zuletzt gesetzt hat
    """
    reasons: list[str] = []
    head = ((pr.get("head") or {}).get("sha") or "") or None

    if pr.get("draft"):
        reasons.append("PR ist ein Entwurf.")
    if pr.get("mergeable") is not True:
        reasons.append(f"GitHub meldet mergeable={pr.get('mergeable')!r}, also nicht sicher mergebar.")
    if not head:
        reasons.append("Head-SHA fehlt; ohne ihn ist keine Freigabe zuordenbar.")

    allowed = {bot.lower()} | {a.lower() for a in trusted}
    if (label_actor or "").lower() not in allowed:
        reasons.append(f"Label `{MERGE_LABEL}` wurde von `{label_actor or 'unbekannt'}` gesetzt; "
                       f"erlaubt sind nur `{bot}` und die Konten aus `{TRUSTED_ACTORS_ENV}`.")

    newest = latest_reviews(reviews)
    bot_review = newest.get(bot.lower())
    if bot_review is None:
        reasons.append(f"Keine Review von `{bot}`.")
    elif bot_review.get("state") != "APPROVED":
        reasons.append(f"Neueste Review von `{bot}` ist {bot_review.get('state')!r}, nicht APPROVED.")
    elif head and bot_review.get("commit_id") != head:
        reasons.append(f"Freigabe von `{bot}` gilt für {str(bot_review.get('commit_id'))[:7]}, "
                       f"nicht für den aktuellen Head {head[:7]}.")

    for login, review in sorted(newest.items()):
        if review.get("state") == "CHANGES_REQUESTED" and (head is None or review.get("commit_id") == head):
            reasons.append(f"Offene Änderungsanforderung von `{login}` auf dem Head-SHA.")

    state = checks.get("state")
    if state != "success":
        reasons.append(f"Kombinierter Status ist {state!r}, nicht 'success'.")
    runs = [r for r in (checks.get("runs") or []) if r.get("name") != SELF_CHECK]
    if not runs:
        reasons.append("Keine fremden Check-Läufe gefunden; nichts geprüft heißt nicht gemergt.")
    bad = [f"{r.get('name')} ({r.get('status')}/{r.get('conclusion')})"
           for r in runs
           if r.get("status") != "completed" or r.get("conclusion") not in OK_CONCLUSIONS]
    if bad:
        reasons.append("Nicht erfolgreich abgeschlossen: " + ", ".join(sorted(bad)))
    guard = [r for r in runs if r.get("name") == REQUIRED_CHECK]
    if not guard:
        reasons.append(f"Check `{REQUIRED_CHECK}` fehlt.")
    elif not any(r.get("conclusion") == "success" for r in guard):
        reasons.append(f"Check `{REQUIRED_CHECK}` ist nicht erfolgreich.")

    return Decision(not reasons, reasons)


class MergeAbgelehnt(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com",
                 graphql_url: str = "https://api.github.com/graphql"):
        self.base = f"{api}/repos/{repo}"
        self.repo = repo
        self.token = token
        self.graphql_url = graphql_url

    def _call(self, method: str, path: str, payload: dict | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise MergeAbgelehnt(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        return json.loads(body) if body.strip() else {}

    def _paged(self, path: str) -> list[dict]:
        items, page = [], 1
        while True:
            batch = self._call("GET", f"{path}?per_page=100&page={page}") or []
            items += batch
            if len(batch) < 100:
                return items
            page += 1

    def reviews(self, number: int) -> list[dict]:
        return self._paged(f"/pulls/{number}/reviews")

    def pull(self, number: int) -> dict:
        """Aktueller PR-Zustand.

        Nötig, weil `mergeable` im Event-Payload von `pull_request_target` häufig `null`
        ist — GitHub berechnet es erst auf Abruf. Ohne diesen Abruf würde das Gate jeden
        Merge blockieren.
        """
        return self._call("GET", f"/pulls/{number}")

    def checks(self, sha: str) -> dict:
        """(Status + Check-Läufe) des Commits als eine Sicht."""
        status = self._call("GET", f"/commits/{sha}/status") or {}
        runs = self._call("GET", f"/commits/{sha}/check-runs") or {}
        return {"state": status.get("state"), "runs": runs.get("check_runs") or []}

    def merge(self, number: int, sha: str) -> dict:
        # sha mitschicken: verschiebt sich der Head, lehnt GitHub ab (409).
        return self._call("PUT", f"/pulls/{number}/merge",
                          {"merge_method": "squash", "sha": sha})

    def delete_branch(self, ref: str) -> None:
        self._call("DELETE", f"/git/refs/heads/{ref}")

    def label_actor(self, number: int, label: str) -> str | None:
        """Login, der `label` am PR zuletzt gesetzt hat — None, wenn nie gesetzt."""
        owner, name = self.repo.split("/", 1)
        query = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      timelineItems(itemTypes: [LABELED_EVENT], last: 100) {
        nodes { ... on LabeledEvent { createdAt actor { login } label { name } } }
      }
    }
  }
}
"""
        req = urllib.request.Request(
            self.graphql_url,
            data=json.dumps({"query": query,
                             "variables": {"owner": owner, "name": name, "number": number}}).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise MergeAbgelehnt(f"GraphQL -> HTTP {e.code}") from None
        if data.get("errors"):
            raise MergeAbgelehnt(f"GraphQL-Fehler: {data['errors']}")
        nodes = data["data"]["repository"]["pullRequest"]["timelineItems"]["nodes"]
        hits = [(n["createdAt"], (n.get("actor") or {}).get("login"))
                for n in nodes if n and (n.get("label") or {}).get("name") == label]
        if not hits:
            return None
        newest = max(hits, key=lambda h: _instant(h[0]))[1]
        return newest.lower() if newest else None


def report(decision: Decision, pr: dict, merged: bool) -> str:
    lines = ["## merge-nach-freigabe", ""]
    kopf = f"PR #{pr.get('number')} · Head `{str((pr.get('head') or {}).get('sha'))[:7]}`"
    if merged:
        lines.append(f"✅ Gemergt (squash): {kopf}")
    elif decision.merge:
        lines.append(f"⚠️ Freigabe lag vor, der Merge ist aber nicht durchgelaufen: {kopf}")
    else:
        lines.append(f"⛔ Nicht gemergt: {kopf}")
    for r in decision.reasons:
        lines.append(f"- ❌ {r}")
    return "\n".join(lines) + "\n"


def main() -> int:
    event = json.loads(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8").read())
    label = (event.get("label") or {}).get("name")
    pr = event.get("pull_request") or {}
    if label != MERGE_LABEL:
        print(f"Label {label!r} löst keinen Merge aus.")
        return 0

    bot = os.environ.get("REVIEWER_BOT", DEFAULT_BOT)
    gh = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql"))
    number = pr["number"]

    merged = False
    zustand = pr
    head = (pr.get("head") or {}).get("sha")
    try:
        # Zustand frisch holen: `mergeable` steht im Event-Payload meist auf null und wird
        # von GitHub erst berechnet — kurz nach einem Push kann es noch null sein.
        fresh = gh.pull(number)
        for _ in range(3):
            if fresh.get("mergeable") is not None:
                break
            time.sleep(2)
            fresh = gh.pull(number)
        zustand = {**pr, "number": number,
                   "draft": fresh.get("draft"),
                   "mergeable": fresh.get("mergeable"),
                   "head": fresh.get("head") or pr.get("head")}
        head = (zustand.get("head") or {}).get("sha")
        decision = decide(zustand, gh.reviews(number), gh.checks(head) if head else {"state": None, "runs": []},
                          gh.label_actor(number, MERGE_LABEL), trusted_actors(), bot)
        if decision.merge and head:
            gh.merge(number, head)
            merged = True
            ref = (zustand.get("head") or {}).get("ref") or ""
            if ref.startswith("feature/"):
                try:
                    gh.delete_branch(ref)
                except MergeAbgelehnt as exc:
                    print(f"Branch `{ref}` nicht gelöscht: {exc}")
    except MergeAbgelehnt as exc:
        # Jeder API-Fehler endet hier: nicht mergen, Grund berichten, Job rot.
        decision = Decision(False, [str(exc)])

    text = report(decision, zustand, merged)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    for r in decision.reasons:
        print(f"::error title=merge-nach-freigabe::{r.splitlines()[0]}")
    return 0 if merged else 1


if __name__ == "__main__":
    raise SystemExit(main())
