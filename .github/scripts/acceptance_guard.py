"""acceptance-guard: prüft einen Pull Request, ohne dessen Code auszuführen.

Warum es das gibt: Ein Worker-Agent, der Tests „grün machen“ soll, kann sie auch grün
machen, indem er sie abschwächt. Dieser Check ist der unabhängige Schiedsrichter:

1. Bereits gemergte Akzeptanztests (tests/acceptance/) sind unveränderlich.
2. Verlinkt der PR ein Issue („Closes #N“) mit Akzeptanztest-Block, muss
   tests/acceptance/test_issue_N.py genau diesem Block entsprechen.
3. Keine neuen skip/xfail-Markierungen in Tests.
4. CI- und Test-Infrastruktur (.github/, conftest.py, pytest-Konfiguration) bleibt unberührt.
5. Der Text eines verlinkten Issues mit Akzeptanztest-Block wird nach der Übernahme
   (erstes Label `in-arbeit`) nicht mehr geändert. Sonst ändert, wer den Text ändert,
   still den Test, gegen den Regel 2 prüft.
6. Der PR-Text ist ausgefüllt: kein „Closes #“ ohne Nummer; ein Worker-Branch
   (`feature/issue-<N>-…`) verlinkt Issue N und nennt „Worker: <Familie> <Modell>“.
   Sonst prüft Regel 2 nichts, der Check wäre trotzdem grün, und kein Reviewer kann die
   Modellfamilie abgleichen.

Verstöße gegen 1, 3, 4 und 5 kann Stephan bewusst mit dem Label `freigabe-geschützt` erlauben.
Regel 2 und 6 kennen keine Ausnahme: Stimmt der Test nicht, wird das Issue korrigiert,
fehlt etwas im PR-Text, wird der Text ergänzt.

Läuft als pull_request_target mit dem Stand aus `main` und liest den PR nur über die
GitHub-API. Nur Standardbibliothek, damit der Job ohne pip auskommt.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable

OVERRIDE_LABEL = "freigabe-geschützt"
CLAIM_LABEL = "in-arbeit"
ACCEPTANCE_DIR = "tests/acceptance/"

# Dateien, über die sich Tests oder CI still aushebeln lassen.
PROTECTED_NAMES = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
PROTECTED_PREFIXES = (".github/",)

_LINK_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n(.*?)\n?^\1[ \t]*$", re.M | re.S)
_BARE_LINK_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(?!\d)", re.I)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_WORKER_BRANCH_RE = re.compile(r"^feature/issue-(\d+)(?:-|$)")
_WORKER_LINE_RE = re.compile(r"^[\s>*_-]*Worker[*_]*\s*:[\s*_]*\w", re.I | re.M)
_SKIP_RE = re.compile(
    r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail|importorskip)\s*\("
)


@dataclass
class Result:
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


# ---------------- Parsing ----------------
def linked_issues(pr_body: str | None) -> list[int]:
    """Issue-Nummern aus „Closes #12“, „fixes #3“, „Resolves: #7“ (Reihenfolge erhalten)."""
    seen: list[int] = []
    for m in _LINK_RE.finditer(pr_body or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def pr_text_violations(head_ref: str | None, pr_body: str | None) -> list[str]:
    """Regel 6: Verstöße im PR-Text. Kommentare der Vorlage (<!-- … -->) zählen nicht."""
    text = _HTML_COMMENT_RE.sub("", pr_body or "")
    found = []
    if _BARE_LINK_RE.search(text):
        found.append("PR-Text enthält „Closes #“ ohne Issue-Nummer (Vorlage nicht ausgefüllt).")
    m = _WORKER_BRANCH_RE.match(head_ref or "")
    if m:
        number = int(m.group(1))
        if number not in linked_issues(text):
            found.append(f"Worker-Branch `{head_ref}`, aber der PR-Text enthält kein „Closes #{number}“.")
        if not _WORKER_LINE_RE.search(text):
            found.append("PR-Text nennt keinen Worker („Worker: <Familie> <Modell>“, AGENTS.md Worker 7).")
    return found


def extract_acceptance_block(issue_body: str | None) -> str | None:
    """Code-Block unter der Überschrift „Akzeptanztests“ oder None.

    Issue-Formulare rendern das Feld als „### Akzeptanztests“ + ```python-Block,
    ein leeres Feld als „_No response_“. Handgeschriebene Issues dürfen jede
    Überschriften-Ebene nutzen.
    """
    lines = (issue_body or "").replace("\r\n", "\n").split("\n")
    start = level = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and m.group(2).lower().startswith("akzeptanztests"):
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    in_fence = False
    for i in range(start, len(lines)):
        if re.match(r"^(`{3,}|~{3,})", lines[i]):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break
    section = "\n".join(lines[start:end])
    fence = _FENCE_RE.search(section)
    if not fence:
        return None
    code = normalize(fence.group(2))
    return code or None


def normalize(code: str) -> str:
    """Zeilenenden und Leerraum am Zeilen-/Dateiende sind keine inhaltliche Änderung."""
    lines = [ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def added_skip_markers(patch: str | None) -> list[str]:
    """Hinzugefügte Diff-Zeilen, die Tests überspringen oder als erwarteten Fehler markieren."""
    hits = []
    for line in (patch or "").split("\n"):
        if line.startswith("+") and not line.startswith("+++") and _SKIP_RE.search(line):
            hits.append(line[1:].strip())
    return hits


def is_protected_infra(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIXES) or PurePosixPath(path).name in PROTECTED_NAMES


def _instant(ts: str) -> datetime:
    # fromisoformat versteht „Z“ erst ab Python 3.11; der Guard soll nicht davon abhängen.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def issue_edit_violation(number: int, last_edited_at: str | None, claimed_at: str | None) -> str | None:
    """Verstoßtext oder None.

    last_edited_at: letzte Änderung des Issue-Texts (ISO 8601), None = nie geändert.
    claimed_at: Zeitpunkt des ersten Label-Ereignisses `in-arbeit`, None = nie übernommen.
    Ohne Übernahme lässt sich nicht belegen, dass der Text beim Arbeitsbeginn schon so
    lautete; deshalb ist das ebenfalls ein Verstoß.
    """
    if claimed_at is None:
        return (f"Issue #{number} wurde nie mit `{CLAIM_LABEL}` übernommen; "
                "unveränderter Issue-Text nicht belegbar.")
    if last_edited_at is None:
        return None
    # Als Zeitpunkte vergleichen: GitHub liefert „Z“, andere Quellen evtl. einen Offset.
    if _instant(last_edited_at) <= _instant(claimed_at):
        return None
    return (f"Text von Issue #{number} wurde nach der Übernahme (`{CLAIM_LABEL}`, {claimed_at}) "
            f"geändert ({last_edited_at}). Damit ändert sich der Akzeptanztest.")


# ---------------- Bewertung ----------------
def evaluate(
    files: list[dict],
    labels: set[str],
    issues: dict[int, str | None],
    read_head_file: Callable[[str], str | None],
    issue_meta: dict[int, tuple[str | None, str | None]] | None = None,
) -> Result:
    """Reine Logik, damit sie ohne GitHub testbar ist.

    files: Einträge aus GET /pulls/{n}/files (filename, status, previous_filename, patch)
    issues: verlinkte Issues -> Body (nur echte Issues, keine PRs)
    read_head_file: liest eine Datei im Stand des PR-Heads, None wenn sie fehlt
    issue_meta: Issue -> (last_edited_at, claimed_at); None = Regel 5 nicht prüfen
    """
    res = Result()
    override = OVERRIDE_LABEL in labels

    def protected(msg: str) -> None:
        if override:
            res.notes.append(f"Erlaubt durch Label `{OVERRIDE_LABEL}`: {msg}")
        else:
            res.violations.append(msg)

    for f in files:
        name, status = f["filename"], f.get("status", "modified")
        old = f.get("previous_filename")

        # 1. Gemergte Akzeptanztests sind unveränderlich; neue dürfen dazukommen.
        touched_existing = [p for p in (name, old) if p and p.startswith(ACCEPTANCE_DIR)]
        if touched_existing and status != "added":
            protected(f"Bestehender Akzeptanztest geändert ({status}): `{old or name}`")

        # 3. Kein neues Überspringen von Tests.
        if name.startswith("tests/") or PurePosixPath(name).name.startswith("test_"):
            if f.get("patch") is None and status not in ("removed",):
                res.notes.append(f"Kein Diff von GitHub für `{name}` (zu groß?), skip-Prüfung nicht möglich.")
            for hit in added_skip_markers(f.get("patch")):
                protected(f"Neues skip/xfail in `{name}`: `{hit}`")

        # 4. CI und Test-Konfiguration.
        for p in {name, old} - {None}:
            if is_protected_infra(p):
                protected(f"CI-/Test-Infrastruktur geändert: `{p}`")

    # 2. Akzeptanztest muss exakt dem Issue entsprechen (keine Ausnahme per Label).
    for number, body in issues.items():
        expected = extract_acceptance_block(body)
        path = f"{ACCEPTANCE_DIR}test_issue_{number}.py"
        if expected is None:
            res.notes.append(f"Issue #{number} hat keinen Akzeptanztest-Block.")
            continue
        # 5. Issue-Text nach der Übernahme geändert?
        if issue_meta is not None and number in issue_meta:
            edit = issue_edit_violation(number, *issue_meta[number])
            if edit:
                protected(edit)
        actual = read_head_file(path)
        if actual is None:
            res.violations.append(f"`{path}` fehlt. Akzeptanztests aus Issue #{number} 1:1 übernehmen.")
            continue
        if normalize(actual) != expected:
            diff = "\n".join(
                difflib.unified_diff(
                    expected.split("\n"), normalize(actual).split("\n"),
                    f"Issue #{number}", path, lineterm="", n=1,
                )
            )
            short = "\n".join(diff.split("\n")[:40])
            res.violations.append(
                f"`{path}` weicht vom Akzeptanztest in Issue #{number} ab. "
                f"Ist der Test falsch, das Issue korrigieren, nicht die Datei.\n```diff\n{short}\n```"
            )
        else:
            res.notes.append(f"`{path}` entspricht Issue #{number}.")

    return res


# ---------------- GitHub-Anbindung ----------------
_ISSUE_TIMES_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      lastEditedAt
      timelineItems(itemTypes: [LABELED_EVENT], first: 100) {
        nodes { ... on LabeledEvent { createdAt label { name } } }
      }
    }
  }
}
"""


class GitHub:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com",
                 graphql_url: str = "https://api.github.com/graphql"):
        self.base = f"{api}/repos/{repo}"
        self.repo = repo
        self.token = token
        self.graphql_url = graphql_url

    def _get(self, path: str, raw: bool = False):
        req = urllib.request.Request(
            self.base + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return data.decode("utf-8", "replace") if raw else json.loads(data)

    def pr_files(self, number: int) -> list[dict]:
        files, page = [], 1
        while True:
            batch = self._get(f"/pulls/{number}/files?per_page=100&page={page}") or []
            files += batch
            if len(batch) < 100:
                return files
            page += 1

    def issue_body(self, number: int) -> tuple[bool, str | None]:
        """(ist_echtes_issue, body)"""
        d = self._get(f"/issues/{number}")
        if not d or "pull_request" in d:
            return False, None
        return True, d.get("body")

    def file_at(self, path: str, ref: str) -> str | None:
        return self._get(f"/contents/{path}?ref={ref}", raw=True)

    def _graphql(self, query: str, variables: dict) -> dict:
        req = urllib.request.Request(
            self.graphql_url,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        # GraphQL meldet Fehler mit HTTP 200; ohne diese Prüfung liefe der Check still grün.
        if data.get("errors"):
            raise RuntimeError(f"GraphQL-Fehler: {data['errors']}")
        return data["data"]

    def issue_times(self, number: int) -> tuple[str | None, str | None]:
        """(last_edited_at, claimed_at) des Issues.

        Fehler werden bewusst nicht abgefangen: der Check soll dann rot werden, nicht still grün.
        """
        owner, name = self.repo.split("/", 1)
        issue = self._graphql(_ISSUE_TIMES_QUERY,
                              {"owner": owner, "name": name, "number": number})["repository"]["issue"]
        # Das erste Ereignis zählt: Ein späteres Entfernen und Neusetzen des Labels darf eine
        # Änderung dazwischen nicht nachträglich legitimieren.
        claims = [
            n["createdAt"] for n in issue["timelineItems"]["nodes"]
            if n and (n.get("label") or {}).get("name") == CLAIM_LABEL
        ]
        claimed = min(claims, key=_instant) if claims else None
        return issue.get("lastEditedAt"), claimed


def report(res: Result) -> str:
    lines = ["## acceptance-guard", ""]
    if res.ok:
        lines.append("✅ Keine Verstöße.")
    for v in res.violations:
        lines.append(f"- ❌ {v}")
    for n in res.notes:
        lines.append(f"- ℹ️ {n}")
    return "\n".join(lines) + "\n"


def main() -> int:
    event = json.loads(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8").read())
    pr = event["pull_request"]
    gh = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"],
                os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                os.environ.get("GITHUB_GRAPHQL_URL", "https://api.github.com/graphql"))

    issues: dict[int, str | None] = {}
    issue_meta: dict[int, tuple[str | None, str | None]] = {}
    for n in linked_issues(pr.get("body")):
        real, body = gh.issue_body(n)
        if real:
            issues[n] = body
            issue_meta[n] = gh.issue_times(n)

    res = evaluate(
        files=gh.pr_files(pr["number"]),
        labels={lab["name"] for lab in pr.get("labels", [])},
        issues=issues,
        read_head_file=lambda p: gh.file_at(p, pr["head"]["sha"]),
        issue_meta=issue_meta,
    )
    if not issues:
        res.notes.append("PR verlinkt kein Issue („Closes #N“); Akzeptanz-Abgleich entfällt.")
    # 6. Ohne Ausnahme per Label: Ein unausgefüllter PR-Text wird korrigiert, nicht erlaubt.
    res.violations += pr_text_violations(pr["head"].get("ref"), pr.get("body"))

    text = report(res)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    for v in res.violations:
        print(f"::error title=acceptance-guard::{v.splitlines()[0]}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
