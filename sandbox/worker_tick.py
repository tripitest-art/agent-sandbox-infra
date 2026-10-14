#!/usr/bin/env python3
"""Taktgeber für den Qwen-Worker in der Sandbox (CT 112), läuft per systemd-Timer.
Eingerichtet von sandbox/install.sh; Anleitung: README.md.

Pro Takt höchstens ein Auftrag, in dieser Reihenfolge:
1. Nacharbeit: offener PR eines agent-qwen-Issues, dessen neuestes Review „Änderungen nötig“
   lautet und jünger ist als der letzte Commit.
2. PR-Text nachtragen: offener PR eines agent-qwen-Issues, dessen Text `Closes #N`, die
   Worker-Zeile oder eine Überschrift der Vorlage fehlt. Das prüft der Takt selbst, weil das
   Modell auch „fertig“ meldet, wenn es den Schritt ausgelassen hat (#112).
3. Neues Issue: worker-task + bereit + agent-qwen, nicht blockiert, von tripitest-art.
   Danach prüft er, ob vom Branch feature/issue-<N> ein PR offen ist.
Vorgabe (WAKE_PC=0): Der Takt läuft alle 5 min, weckt den Gaming-PC aber nie. Zuerst liest er
/proxy/status (weckt nie); nur wenn der PC an ist und Chat aktiv, sucht er Arbeit auf GitHub.
Mit WAKE_PC=1 wird erst bei Arbeit gefragt und ein schlafender PC per WoL geweckt.
Läuft dort ein anderes Modell (Chat, Bilder), wartet der Takt auf die nächste Runde.
Nach einer Nacharbeit prüft er, ob ein neuer Commit oder ein geänderter PR-Text ankam.

Den Auftrag führt Qwen Code aus (AGENT=qwen-code, Vorgabe) oder OpenCode (AGENT=opencode).

Modell (BACKEND): `ollama` = Qwen3-Coder auf dem Gaming-PC über den WoL-Proxy (alles oben);
`litellm` = Modell-Alias LITELLM_MODEL (Vorgabe `coder`, DeepSeek) über den LiteLLM-Proxy
(LXC 113). Dann gibt es keine PC-Prüfung und kein Wecken; der Takt prüft nur, ob LiteLLM
antwortet. Den Key (LITELLM_API_KEY) liest systemd aus /etc/qwen-worker.env, nie aus dem Repo.
Vor jedem Lauf schickt der Takt eine Mini-Anfrage an LITELLM_MODEL. Antwortet es nicht binnen
PROBE_TIMEOUT, nimmt er das erste Ausweichmodell aus LITELLM_FALLBACKS, das antwortet; die
Worker-Zeile nennt dann dieses Modell. Antwortet keins, wartet er, ohne einen Versuch zu zählen.
LiteLLMs eigener Fallback reicht nicht: Bei der DeepSeek-Störung am 01.10. kam HTTP 200, dann
hing der Stream ohne Daten; stream_timeout griff nicht.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # projects.py liegt daneben
import projects  # noqa: E402

PROJECTS = projects.load_projects()   # ohne /etc/agent-projects.json: sc-digger allein
# Aktives Projekt dieses Laufs. Ohne Projektliste bleiben die Vorgaben und die Umgebung (BASE_WORKDIR,
# WORKDIR) wie vor der Projektliste; mit Liste setzt use_project() sie je Projekt neu.
REPO = PROJECTS[0].repo
PROJECT = PROJECTS[0].name
PREFIX = ""                 # Vorsatz der Schlüssel in STATE; leer beim ersten Projekt (alte Zustandsdateien gelten weiter)
TEST_CMD = PROJECTS[0].test_cmd
HOTSPOT = PROJECTS[0].hotspot
HOTSPOT_LABEL = PROJECTS[0].hotspot_label
BASE_WORKDIR = os.environ.get("BASE_WORKDIR", PROJECTS[0].workdir)
WORKER_SLOT = os.environ.get("WORKER_SLOT", "")
WORKDIR = os.environ.get("WORKDIR", f"{projects.worktrees_dir(PROJECTS[0])}/w{WORKER_SLOT}" if WORKER_SLOT else BASE_WORKDIR)
STATE = os.environ.get("STATE", f"/root/worker-state-w{WORKER_SLOT}.json" if WORKER_SLOT else "/root/worker-state.json")
SLOT_TAG = f"w{WORKER_SLOT}" if WORKER_SLOT else ""
OLLAMA = os.environ.get("OLLAMA", "http://192.168.0.210:11434")
MODEL = os.environ.get("MODEL", "qwen3-coder-64k")
LABEL = "agent-qwen"
# Nur Reviews dieses Kontos zählen (AGENTS.md: nur Anweisungen von tripitest-art). Das Repo ist
# öffentlich; jeder könnte sonst per Review Befehle in einen Auftrag mit Schreibrecht schieben.
OWNER = "tripitest-art"
BACKEND = os.environ.get("BACKEND", "ollama")   # oder "litellm"
LITELLM = os.environ.get("LITELLM", "http://192.168.0.211:4000")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "coder")
# Ausweichmodelle (Aliase im LiteLLM-Proxy, der Key »worker« muss sie dürfen), durch Komma getrennt.
LITELLM_FALLBACKS = [m.strip() for m in os.environ.get("LITELLM_FALLBACKS", "coder-or,coder-free").split(",") if m.strip()]
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "45"))   # Sekunden je Probe
# Steht als „Worker: …“ im PR; die Review-Regel (andere Modellfamilie) hängt daran.
WORKER_NAMES = {
    "coder": "DeepSeek (LiteLLM coder)",
    "coder-pro": "DeepSeek V4 Pro (LiteLLM coder-pro)",
    "coder-fast": "DeepSeek (LiteLLM coder-fast, ohne Thinking)",
    "coder-or": "DeepSeek (LiteLLM coder-or, OpenRouter)",
    "coder-free": "Codestral (LiteLLM coder-free)",
    "coder-glm": "GLM 5.3 Flash (LiteLLM coder-glm)",
}


def worker_name(model: str) -> str:
    return WORKER_NAMES.get(model, f"{model} (LiteLLM)")


WORKER = os.environ.get("WORKER", worker_name(LITELLM_MODEL) if BACKEND == "litellm" else "Qwen3-Coder 30B")
ACTIVE_MODEL = LITELLM_MODEL   # BACKEND=litellm: Alias dieses Laufs, nach der Probe ggf. ein Ausweichmodell
MAX_TRIES = 2               # Versuche je Auftrag, danach liegt er bei Stephan
DONE = "erledigt"           # Schlüssel in STATE: angekommene Nacharbeiten, nicht wiederholen
# Minuten je Lauf des Agenten. Über LiteLLM kürzer: das Modell ist schneller, und jede Minute
# kostet Token; 90 min hat bei #122 nur Zeit verbrannt.
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT_MIN", "30" if BACKEND == "litellm" else "90")) * 60
AGENT = os.environ.get("AGENT", "qwen-code")   # oder "opencode"
PR_TEMPLATE = ".github/pull_request_template.md"
TOOL_OUTPUT_LINES = 15      # so viele Zeilen je Werkzeugausgabe ins Log
# Sekunden, die der Takt aufs Wecken wartet; länger als WAKE_TIMEOUT des Proxys (180 s),
# damit der Proxy aufgibt und nicht der Takt.
WAKE_WAIT = int(os.environ.get("WAKE_WAIT", "240"))
# 1: bei Arbeit den Gaming-PC per WoL wecken. 0 (Vorgabe): nur arbeiten, wenn er an ist und
# Chat aktiv; dafür läuft der Takt oft (install.sh, TAKT).
WAKE_PC = os.environ.get("WAKE_PC", "0") == "1"
# Schlüssel in STATE: letzter Zustand von PC und Arbeit (Meldung nur beim Wechsel)
LAST_PC, LAST_WORK = "_pc", "_arbeit"

# Wie .github/scripts/acceptance_guard.py: Kommentare der Vorlage zählen nicht.
_LINK_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)
_WORKER_LINE_RE = re.compile(r"^[\s>*_-]*Worker[*_]*\s*:[\s*_]*\w", re.I | re.M)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# In beiden Aufträgen: Qwen hielt „GraphQL: …“ von gh für Erfolg (#103).
ERROR_RULE = (
    "Gibt ein Befehl eine Fehlermeldung aus (z. B. „GraphQL: …“, „error“, Exit-Code ungleich 0), ist\n"
    "der Schritt NICHT erledigt, auch wenn danach nichts mehr kommt. Beheben und wiederholen; geht\n"
    "das nicht, nicht „fertig“ melden, sondern den Fehler wörtlich als Kommentar {where} schreiben\n"
    "und aufhören.")

ENV = dict(os.environ, PATH=f"/root/venv/bin:/root/.opencode/bin:{os.environ.get('PATH', '/usr/bin:/bin')}")


def log(msg: str) -> None:
    prefix = f" [{SLOT_TAG}]" if SLOT_TAG else ""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}{prefix} {msg}", flush=True)


def use_project(p: "projects.Project", first: bool) -> None:
    """Schaltet die Globalen auf das Projekt `p` um (Repo, Arbeitsordner, Testbefehl, Hotspot)."""
    global REPO, PROJECT, PREFIX, TEST_CMD, HOTSPOT, HOTSPOT_LABEL, BASE_WORKDIR, WORKDIR
    REPO, PROJECT, TEST_CMD, HOTSPOT, HOTSPOT_LABEL = p.repo, p.name, p.test_cmd, p.hotspot, p.hotspot_label
    PREFIX = "" if first else f"{p.name}/"
    BASE_WORKDIR = p.workdir
    WORKDIR = f"{projects.worktrees_dir(p)}/w{WORKER_SLOT}" if WORKER_SLOT else p.workdir


def gh(*args: str):
    out = subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=True, env=ENV)
    return json.loads(out.stdout) if out.stdout.strip() else None


def pr_template() -> str:
    for base in (WORKDIR, BASE_WORKDIR):
        try:
            with open(os.path.join(base, PR_TEMPLATE), encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            pass
    return ""


def pr_text_problems(body: str | None, issue_no: int) -> list[str]:
    """Was im PR-Text fehlt: `Closes #N`, Worker-Zeile, Überschriften der Vorlage."""
    text = _HTML_COMMENT_RE.sub("", body or "")
    found = []
    if issue_no not in {int(n) for n in _LINK_RE.findall(text)}:
        found.append(f"„Closes #{issue_no}“")
    if not _WORKER_LINE_RE.search(text):
        found.append(f"Zeile „Worker: {WORKER}“")
    headings = {line.strip() for line in text.splitlines()}
    for line in pr_template().splitlines():
        if line.startswith("## ") and line.strip() not in headings:
            found.append(f"Überschrift „{line.strip()}“")
    return found


def template_rules() -> str:
    """Vorlage wörtlich in den Auftrag: Mit nur dem Dateinamen schrieb Qwen eigene Überschriften
    ohne „Closes #N“ und hakte Tests ab, die es nicht gab (#112)."""
    return f"""Regeln für den PR-Text: genau die Überschriften der Vorlage unten, in dieser Reihenfolge.
Die Kommentare <!-- … --> ersetzt du durch Inhalt. Ein Kästchen [x] nur, wenn es stimmt und du es
geprüft hast; was es nicht gibt, bleibt [ ]. Unter „Offene Punkte“ steht eine Antwort („keine“ reicht).

----- PR-Vorlage ({PR_TEMPLATE}) -----
{pr_template().strip()}
----- Ende PR-Vorlage -----"""


def load_state() -> dict:
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=1)


def review_is_current(review: dict, pr: dict) -> bool:
    """Bezieht sich das Review auf den aktuellen Stand des PRs?

    Maßgeblich ist der geprüfte Commit, nicht das Datum: committedDate ist die Zeit des lokalen
    Commits, nicht des Pushs. Ein vor dem Review erstellter, danach gepushter Commit sähe sonst
    wie „noch nicht nachgebessert“ aus. Ohne Commit-Angabe (ältere gh-Version) bleibt nur das Datum.
    """
    oid = (review.get("commit") or {}).get("oid")
    if oid:
        return oid == pr["headRefOid"]
    return review["submittedAt"] > max(c["committedDate"] for c in pr["commits"])


def pr_snapshot(pr: dict) -> tuple:
    """Stand eines PRs, an dem sich eine Nacharbeit zeigen muss: Commit, Titel oder Text.

    Nur auf den Commit zu schauen reicht nicht: Verlangt ein Review nur den PR-Text (#103), ist
    „kein neuer Commit“ richtig und die Nacharbeit trotzdem erledigt.
    """
    return pr["headRefOid"], pr.get("title", ""), (pr.get("body") or "").replace("\r\n", "\n")


def rework_job(done=frozenset()):
    """(Schlüssel, Auftrag, PR-Nummer, pr_snapshot) für die älteste fällige Nacharbeit oder None.

    `done`: Schlüssel schon angekommener Nacharbeiten. Betraf sie nur den PR-Text, bleibt der
    Commit gleich und das Review sähe weiter fällig aus; ohne diese Liste lief #103 erneut.

    Das Review steht wörtlich im Auftrag: Qwen hat es sonst nicht gelesen, nur die Tests auf
    main laufen lassen und den PR selbst für fertig erklärt (#103, erster Lauf).
    """
    prs = gh("pr", "list", "--state", "open", "--json",
             "number,headRefName,headRefOid,isCrossRepository,reviews,commits,title,body")
    for pr in sorted(prs, key=lambda p: p["number"]):
        m = re.match(r"feature/issue-(\d+)", pr["headRefName"])
        if not m or pr.get("isCrossRepository") or not pr["commits"]:
            continue  # Fork-PRs nie: Branch-Name und Inhalt bestimmt dort ein Fremder
        reviews = [r for r in pr["reviews"] if (r.get("author") or {}).get("login") == OWNER]
        if not reviews:
            continue
        issue_no = m.group(1)
        issue = gh("issue", "view", issue_no, "--json", "labels")
        if LABEL not in {lab["name"] for lab in issue["labels"]}:
            continue
        review = max(reviews, key=lambda r: r["submittedAt"])
        key = f"pr{pr['number']}@{review['submittedAt']}"
        if key in done:
            continue
        if review_is_current(review, pr) and "Änderungen nötig" in review["body"][:80]:
            n = pr["number"]
            task = f"""Du arbeitest ein Review ab (AGENTS.md, Worker, Schritt 8). Du bist Worker: {WORKER}.
Gehe genau diese Schritte durch und führe jeden als Befehl aus:

1. `gh pr checkout {n}` (du bist danach auf dem PR-Branch, nicht auf main).
2. Lies das Review unten. Setze jeden „Muss“-Punkt um. „Kann“-Punkte nur, wenn sie im Umfang
   von Issue #{issue_no} liegen. Nie Dateien unter tests/acceptance/ ändern.
3. Ist der PR-Text gefordert: schreib ihn in .git/pr-body.md nach der Vorlage unten,
   mit „Closes #{issue_no}“ und „Worker: {WORKER}“, und führe aus:
   `gh pr edit {n} --title "$(gh issue view {issue_no} --json title -q .title)" --body-file .git/pr-body.md`
4. `{TEST_CMD}` muss komplett grün sein.
5. Alle Code-Änderungen in einem Commit, dann `git push`. Verlangt das Review nur den PR-Text,
   gibt es keinen Commit.
6. Prüfe mit `git log -1 --oneline` und `gh pr view {n} --json title,body`, dass Commit und Text
   angekommen sind: Die Ausgabe muss deinen neuen Titel und Text zeigen.
{ERROR_RULE.format(where=f"in den PR #{n}")}
Du gibst den PR nie selbst frei und mergst nie; das macht der Reviewer.

{template_rules()}

----- Review -----
{review['body']}
----- Ende Review -----"""
            return key, task, n, pr_snapshot(pr)
    return None


def text_fix_job():
    """(Schlüssel, Auftrag, PR-Nummer, pr_snapshot) für den ältesten PR mit unvollständigem Text.

    Der Schlüssel hängt am Head-Commit: Neue Commits geben einen neuen Anlauf, sonst gilt
    MAX_TRIES wie für jeden Auftrag.
    """
    prs = gh("pr", "list", "--state", "open", "--json",
             "number,headRefName,headRefOid,isCrossRepository,title,body")
    for pr in sorted(prs or [], key=lambda p: p["number"]):
        m = re.match(r"feature/issue-(\d+)", pr["headRefName"])
        if not m or pr.get("isCrossRepository"):
            continue
        issue_no = int(m.group(1))
        missing = pr_text_problems(pr.get("body"), issue_no)
        if not missing:
            continue
        issue = gh("issue", "view", str(issue_no), "--json", "labels")
        if LABEL not in {lab["name"] for lab in issue["labels"]}:
            continue
        n = pr["number"]
        task = f"""Im Text von PR #{n} (Issue #{issue_no}) fehlt: {", ".join(missing)}.
Du bist Worker: {WORKER}. Du änderst nur den PR-Text, keinen Code, keinen Commit.
Gehe genau diese Schritte durch und führe jeden als Befehl aus:

1. `gh pr diff {n}` und `gh issue view {issue_no}` lesen: Was wurde wirklich geändert und getestet?
2. Schreib den ganzen PR-Text neu in .git/pr-body.md, nach der Vorlage unten, mit „Closes #{issue_no}“
   unter „Was und warum“ und „Worker: {WORKER}“ unter „Umgesetzt von / Review durch“.
3. `gh pr edit {n} --title "$(gh issue view {issue_no} --json title -q .title)" --body-file .git/pr-body.md`
4. Prüfe mit `gh pr view {n} --json title,body`, dass dein neuer Text da ist.
{ERROR_RULE.format(where=f"in den PR #{n}")}

{template_rules()}"""
        return f"text{n}@{pr['headRefOid'][:7]}", task, n, pr_snapshot(pr)
    return None


def extract_section(md: str, heading: str, until: tuple[str, ...] = ()) -> str:
    """Von der Zeile `heading` bis vor die nächste Überschrift gleicher oder höherer Ebene
    (oder vor eine Zeile aus `until`). Leer, wenn die Überschrift fehlt."""
    level = len(heading) - len(heading.lstrip("#"))
    out, active = [], False
    for line in (md or "").splitlines():
        if not active:
            if line.strip() == heading.strip():
                active = True
            continue
        stripped = line.strip()
        if until and any(stripped.startswith(u) for u in until):
            break
        if stripped.startswith("#"):
            l = len(stripped) - len(stripped.lstrip("#"))
            if l <= level:
                break
        out.append(line)
    return "\n".join(out).strip()


def parse_affected_files(issue_body: str) -> set[str]:
    """Extrahiert alle betroffenen Dateipfade aus der Sektion '### Betroffene Dateien'."""
    sec = extract_section(issue_body, "### Betroffene Dateien")
    if not sec:
        return set()
    files = set()
    for m in re.finditer(r"`([\w./-]+\.[a-zA-Z0-9]+)`", sec):
        p = m.group(1).strip()
        if "/" in p or p.endswith((".py", ".yaml", ".yml", ".json", ".txt", ".md", ".sh", ".sql")):
            if not re.match(r"^tests/acceptance/test_issue_\d+\.py$", p):
                files.add(p)
    for m in re.finditer(r"^\s*[-*]\s+([\w./-]+\.[a-zA-Z0-9]+)", sec, re.M):
        p = m.group(1).strip()
        if not re.match(r"^tests/acceptance/test_issue_\d+\.py$", p):
            files.add(p)
    return files


def touches_main_py(issue: dict) -> bool:
    """Prüft, ob ein Issue den Hotspot des Projekts (sc-digger: sc_digger/main.py) berührt, per Label oder Issue-Body."""
    if not HOTSPOT:
        return False
    labels = {lab.get("name", "") if isinstance(lab, dict) else str(lab)
              for lab in issue.get("labels", [])}
    if HOTSPOT_LABEL in labels:
        return True
    body = issue.get("body", "") or ""
    sec = extract_section(body, f"### Berührt {HOTSPOT}").strip().lower()
    if sec.startswith("ja"):
        return True
    return HOTSPOT in parse_affected_files(body)


def active_in_progress_issues() -> list[dict]:
    """Alle aktuell offenen Issues mit Label 'in-arbeit' von tripitest-art."""
    try:
        active = gh("issue", "list", "--state", "open", "--label", "in-arbeit",
                    "--json", "number,title,body,author,labels")
        return [i for i in active or [] if (i.get("author") or {}).get("login") == OWNER]
    except Exception as e:
        log(f"Warnung: Abfrage aktiver Issues fehlgeschlagen: {e}")
        return []


def claim_issue(n: int) -> bool:
    """Wechselt atomar die Labels von 'bereit' zu 'in-arbeit' vor dem Start."""
    try:
        gh("issue", "edit", str(n), "--add-label", "in-arbeit", "--remove-label", "bereit")
        log(f"Issue #{n} erfolgreich auf 'in-arbeit' gesetzt.")
        return True
    except Exception as e:
        log(f"Claiming von Issue #{n} fehlgeschlagen: {e}")
        return False


def issue_branch(n: int) -> str:
    # Fester Name statt eines vom Modell erfundenen: der Taktgeber findet den PR danach wieder,
    # und acceptance-guard erkennt feature/issue-<N> als Worker-Branch.
    return f"feature/issue-{n}"


def issue_job():
    """(Schlüssel, Auftrag, None, None) für das älteste freie Issue ohne Konflikt oder None.

    Der Auftrag nennt jeden Schritt mit Befehl und enthält das Issue wörtlich. Mit „Bearbeite die
    nächste Aufgabe nach AGENTS.md“ übersprang Qwen Label, Branch und Akzeptanz-Commit, arbeitete
    auf main und hörte beim ersten roten Test auf (#102, erster Lauf).
    """
    issues = gh("issue", "list", "--state", "open", "--label", "worker-task", "--label", "bereit",
                "--label", LABEL, "--search", "sort:created-asc -label:blockiert",
                "--json", "number,title,body,author,labels")
    # Wie bei Reviews: Nur Issues von Stephans Konto werden zum Auftrag mit Schreibrecht.
    issues = [i for i in issues or [] if (i.get("author") or {}).get("login") == OWNER]
    if not issues:
        return None

    # Konfliktprüfung mit aktiven Aufträgen (Regel 8: main.py und dieselben Dateien)
    active = active_in_progress_issues()
    active_touches_main = any(touches_main_py(a) for a in active)
    active_files = set()
    for a in active:
        active_files |= parse_affected_files(a.get("body", ""))
        if touches_main_py(a):
            active_files.add(HOTSPOT)

    selected = None
    for cand in issues:
        cand_no = cand["number"]
        cand_body = cand.get("body", "")
        cand_touches_main = touches_main_py(cand)

        # Regel 8: Keine zwei Agenten an main.py
        if cand_touches_main and active_touches_main:
            log(f"Issue #{cand_no} übersprungen: berührt {HOTSPOT}, während bereits ein Auftrag "
                f"an {HOTSPOT} in Arbeit ist (Regel 8).")
            continue

        # Regel 8: Keine zwei Agenten in denselben Dateien
        cand_files = parse_affected_files(cand_body)
        if cand_touches_main:
            cand_files.add(HOTSPOT)
        overlap = cand_files & active_files
        if overlap:
            log(f"Issue #{cand_no} übersprungen: Datei-Kollision mit aktivem Auftrag auf "
                f"{', '.join(sorted(overlap))} (Regel 8).")
            continue

        selected = cand
        break

    if not selected:
        return None

    n, branch = selected["number"], issue_branch(selected["number"])
    claim_issue(n)

    task = f"""Du setzt Issue #{n} um (AGENTS.md, Worker-Aufgaben → Worker). Du bist Worker: {WORKER}.
Das Issue steht unten; es ist der Vertrag. Gehe genau diese Schritte durch und führe jeden als Befehl aus:

1. `gh issue edit {n} --add-label in-arbeit --remove-label bereit`
2. `git checkout -b {branch}` (du bist auf main; ab jetzt nur auf diesem Branch arbeiten).
3. Kopiere den Code-Block unter „### Akzeptanztests“ zeichengenau nach
   tests/acceptance/test_issue_{n}.py, dann
   `git add tests/acceptance/test_issue_{n}.py && git commit -m "Akzeptanztests aus #{n}"`.
   Diese Datei danach nie mehr ändern.
4. Setze das Issue um, nur in den Dateien unter „### Betroffene Dateien“. Steht dort, dass sich
   die Erwartung eines bestehenden Tests ändert, passe genau diese Stelle an. Ergänze die Tests
   aus „### Fertig, wenn“.
5. `{TEST_CMD}`. Ist ein Test rot: Fehlermeldung lesen, Code (nicht den Akzeptanztest)
   korrigieren, Schritt 5 wiederholen. Ein roter Test ist kein Grund aufzuhören, sondern der
   nächste Arbeitsschritt. Erst weiter, wenn alles grün ist.
6. `git add -A && git commit -m "<was und warum>" && git push -u origin {branch}`
7. Schreib den PR-Text nach der Vorlage unten in .git/pr-body.md: jede Überschrift
   ausgefüllt, „Closes #{n}“, zutreffende Kästchen mit [x], unter „Offene Punkte“ eine Antwort
   („keine“ reicht), unter „Umgesetzt von / Review durch“ die Zeile „Worker: {WORKER}“. Dann
   `gh pr create --title "$(gh issue view {n} --json title -q .title)" --body-file .git/pr-body.md`
8. Prüfe mit `gh pr view {branch} --json number,title,body`, dass der PR mit deinem Text da ist.
Hältst du einen Akzeptanztest für falsch: aufhören und die Begründung mit
`gh issue comment {n} --body-file .git/frage.md` ins Issue schreiben. Nie den Test passend machen.
{ERROR_RULE.format(where=f"ins Issue #{n}")}
Du gibst den PR nie selbst frei und mergst nie; das macht der Reviewer.

{template_rules()}

----- Issue #{n}: {selected['title']} -----
{selected['body']}
----- Ende Issue -----"""
    return f"issue{n}", task, None, None


def check_issue_result(key: str) -> None:
    """Nicht still scheitern: Gibt es nach dem Lauf einen PR vom Branch des Issues?"""
    n = int(key.removeprefix("issue"))
    prs = gh("pr", "list", "--state", "open", "--head", issue_branch(n), "--json", "number,body")
    if prs:
        log(f"{key}: PR #{prs[0]['number']} von {issue_branch(n)} ist offen.")
        warn_text(key, prs[0]["number"], prs[0].get("body"), n)
    else:
        log(f"WARNUNG {key}: kein PR von {issue_branch(n)}, Issue nicht erledigt.")


def warn_text(key: str, pr_no: int, body: str | None, issue_no: int) -> None:
    missing = pr_text_problems(body, issue_no)
    if missing:
        log(f"WARNUNG {key}: im Text von PR #{pr_no} fehlt {', '.join(missing)}; "
            "nächster Takt trägt ihn nach.")


def agent_cmd(prompt: str) -> list[str]:
    if AGENT == "opencode":
        model = f"litellm/{ACTIVE_MODEL}" if BACKEND == "litellm" else f"ollama/{MODEL}"
        return ["opencode", "run", "-m", model, prompt]
    # stream-json statt text: Sonst zeigt das Log nur den Schlussbericht, nicht die Befehle.
    cmd = ["qwen", "-p", prompt, "--yolo", "--output-format", "stream-json",
           "--max-wall-time", f"{RUN_TIMEOUT // 60}m"]
    if BACKEND == "litellm":
        cmd += ["-m", ACTIVE_MODEL]   # muss in ~/.qwen/settings.json (modelProviders) stehen
    return cmd


def _clip(text: str, lines: int = TOOL_OUTPUT_LINES) -> str:
    rows = text.rstrip().splitlines()
    more = f"\n    … ({len(rows) - lines} Zeilen mehr)" if len(rows) > lines else ""
    return "\n".join(f"    {r}" for r in rows[:lines]) + more


def log_stream_line(line: str) -> None:
    """Eine Zeile stream-json von Qwen Code als lesbares Log: Text, `$ befehl`, Ausgabe."""
    try:
        event = json.loads(line)
    except ValueError:
        if line.strip():
            print(line.rstrip(), flush=True)
        return
    kind = event.get("type")
    content = (event.get("message") or {}).get("content") or []
    if kind == "assistant":
        for part in content:
            if part.get("type") == "text" and part.get("text", "").strip():
                print(part["text"].strip(), flush=True)
            elif part.get("type") == "tool_use":
                args = part.get("input") or {}
                if part.get("name") == "run_shell_command":
                    print(f"$ {args.get('command', '')}", flush=True)
                else:
                    target = args.get("file_path") or args.get("path") or args.get("pattern") or ""
                    print(f"→ {part.get('name')} {target}".rstrip(), flush=True)
    elif kind == "user":
        for part in content:
            if part.get("type") == "tool_result":
                out = part.get("content")
                out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                mark = "✗ Fehler:\n" if part.get("is_error") else ""
                print(mark + _clip(out), flush=True)
    elif kind == "result":
        print(f"[Qwen Code: {event.get('subtype')}, {event.get('num_turns')} Züge, "
              f"{round((event.get('duration_ms') or 0) / 1000)} s]", flush=True)


def run_agent(prompt: str) -> int:
    """Startet den Agenten; `timeout` beendet ihn hart, falls er selbst nicht aufhört."""
    cmd = ["timeout", "-k", "30", str(RUN_TIMEOUT + 60), *agent_cmd(prompt)]
    with subprocess.Popen(cmd, cwd=WORKDIR, env=ENV, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                          errors="replace") as proc:
        for line in proc.stdout:
            if AGENT == "opencode":
                print(line, end="", flush=True)
            else:
                log_stream_line(line)
    return proc.returncode


def _get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def pc_status() -> dict | None:
    """/proxy/status des WoL-Proxys; weckt den PC nie. None ohne Proxy (Ollama direkt)."""
    try:
        return _get_json(f"{OLLAMA}/proxy/status", 10)
    except Exception:
        return None


def pc_ready(status: dict | None) -> str | None:
    """Ohne WAKE_PC: None, wenn der Takt arbeiten darf, sonst der Grund, warum nicht.

    Der Takt läuft oft (alle 5 min) und soll den PC nie wecken: Er arbeitet nur, wenn der PC
    ohnehin an ist und Chat aktiv. Ohne Proxy (Ollama direkt) gilt der PC als bereit.
    """
    if status is None:
        return None
    if status.get("mode") == "bild":
        return "Gaming-PC im Bildmodus"
    if not status.get("ollama_up"):
        return "Gaming-PC aus, kein Wecken"
    return None


def litellm_ready() -> str | None:
    """BACKEND=litellm: None, wenn der LiteLLM-Proxy antwortet, sonst der Grund.

    /health/liveliness braucht keinen Key und kostet nichts. Budget und Modellrechte prüft
    LiteLLM erst beim Auftrag; ein 429 (Budget aufgebraucht) sieht man dann im Log des Laufs.
    """
    try:
        with urllib.request.urlopen(f"{LITELLM}/health/liveliness", timeout=10):
            return None
    except Exception:
        return "LiteLLM nicht erreichbar"


def probe_model(model: str) -> str | None:
    """None, wenn `model` über LiteLLM binnen PROBE_TIMEOUT antwortet, sonst der Grund.

    Ohne Streaming: Dann schickt LiteLLM erst mit der fertigen Antwort Daten, und der Timeout
    greift auch, wenn der Anbieter die Verbindung nur offen hält. disable_fallbacks, damit die
    Probe das Modell selbst prüft und nicht still dessen Fallback. Der Key erscheint nie im Log.
    """
    body = json.dumps({"model": model, "max_tokens": 16, "stream": False, "disable_fallbacks": True,
                       "messages": [{"role": "user", "content": "Antworte nur mit: ok"}]}).encode()
    req = urllib.request.Request(
        f"{LITELLM}/v1/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('LITELLM_API_KEY', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:  # Timeout, Verbindung, kaputtes JSON: nur der Typ
        return type(e).__name__
    return None if isinstance(data, dict) and data.get("choices") else "leere Antwort"


def choose_model() -> tuple[str | None, list[str]]:
    """Erstes Modell aus LITELLM_MODEL + LITELLM_FALLBACKS, das antwortet, und die Fehlschläge davor."""
    failed = []
    for model in dict.fromkeys([LITELLM_MODEL, *LITELLM_FALLBACKS]):
        reason = probe_model(model)
        if reason is None:
            return model, failed
        failed.append(f"{model}: {reason}")
    return None, failed


def use_model(model: str) -> None:
    """Modell für diesen Lauf; die Worker-Zeile nennt bei einem Ausweichmodell dieses."""
    global ACTIVE_MODEL, WORKER
    if model != ACTIVE_MODEL:
        ACTIVE_MODEL, WORKER = model, worker_name(model)


def project_done(state: dict) -> frozenset:
    """Erledigte Nacharbeiten des aktiven Projekts, ohne Projektvorsatz. Beim ersten Projekt zählen
    nur Schlüssel ohne „/“ (so waren sie schon immer), bei den weiteren nur die mit eigenem Vorsatz."""
    keys = state.get(DONE, [])
    if PREFIX:
        return frozenset(k[len(PREFIX):] for k in keys if k.startswith(PREFIX))
    return frozenset(k for k in keys if "/" not in k)


def find_job(state: dict):
    return rework_job(project_done(state)) or text_fix_job() or issue_job()


def pick_job(state: dict):
    """Erste Arbeit im ersten Projekt, das welche hat (Reihenfolge der Projektliste); None, wenn nirgends.

    Mit nur einem Projekt unverändert find_job(). Ein Projekt, dessen Klon fehlt, wird einmal gemeldet
    und übersprungen. Nach einem Treffer bleibt dieses Projekt aktiv, die Globalen gehören ihm."""
    if len(PROJECTS) == 1:
        return find_job(state)
    for i, p in enumerate(PROJECTS):
        if not os.path.isdir(p.workdir):
            log_on_change(state, f"_fehlt:{p.name}", "fehlt",
                          f"Projekt {p.name}: Klon {p.workdir} fehlt, übersprungen (install.sh ausführen).")
            continue
        use_project(p, first=(i == 0))
        job = find_job(state)
        if job:
            return job
    return None


def log_on_change(state: dict, slot: str, what: str, msg: str) -> bool:
    """Zustandsmeldung nur beim Wechsel; sonst stünde sie alle 5 min im Log."""
    if state.get(slot) == what:
        return False
    log(msg)
    state[slot] = what
    save_state(state)
    return True


def ollama_free(status: dict | None = None, say=log) -> bool:
    """Ollama bereit und frei für MODEL?

    Mit WAKE_PC=1 weckt die Anfrage einen schlafenden PC: /api/ps über den Proxy hält sie, bis
    Ollama antwortet (aus S5 etwa 45–50 s). Mit 15 s Timeout gab der Takt früher auf, der PC
    lief leer, KDE schaltete ihn ab, und es begann von vorn, ohne dass der Auftrag je lief.
    Ohne WAKE_PC kommen die geladenen Modelle aus /proxy/status; das weckt nie, auch nicht,
    wenn der PC gerade ausgeht.
    """
    if status is None:
        status = pc_status()
    if status and status.get("mode") == "bild":
        log("Gaming-PC im Bildmodus, nächste Runde.")
        return False
    if status and not WAKE_PC:
        if not status.get("ollama_up"):
            log("Gaming-PC aus, kein Wecken, nächste Runde.")
            return False
        busy = [m["name"] for m in status.get("models") or [] if not m["name"].startswith(MODEL)]
        if busy:
            say(f"Ollama belegt ({', '.join(busy)}), nächste Runde.")
            return False
        return True
    waking = bool(status) and not status.get("ollama_up")
    if waking:
        log("Gaming-PC ist aus, wecke ihn über den WoL-Proxy.")
    started = time.monotonic()
    try:
        loaded = [m["name"] for m in _get_json(f"{OLLAMA}/api/ps", WAKE_WAIT).get("models", [])]
    except Exception as e:  # Wecken gescheitert, Bildmodus (Proxy blockt) oder Netz weg
        log(f"Ollama nicht erreichbar ({type(e).__name__}), nächste Runde.")
        return False
    if waking:
        log(f"Gaming-PC wach nach {round(time.monotonic() - started)} s.")
    busy = [n for n in loaded if not n.startswith(MODEL)]
    if busy:
        say(f"Ollama belegt ({', '.join(busy)}), nächste Runde.")
        return False
    return True


def prepare_workdir() -> None:
    """Bereitet das Arbeitsverzeichnis für einen neuen Lauf vor (main oder worktree)."""
    base = BASE_WORKDIR if os.path.exists(BASE_WORKDIR) else WORKDIR
    if WORKDIR == base:
        for cmd in (["git", "fetch", "-q", "origin"], ["git", "checkout", "-qf", "main"],
                    ["git", "reset", "-q", "--hard", "origin/main"], ["git", "clean", "-qfd"]):
            subprocess.run(cmd, cwd=WORKDIR, check=True, env=ENV)
        return

    # Worktree-Modus: Basis-Repo fetchen (mit Lock gegen parallele Fetches)
    fetch_lock = os.path.join(base, ".git", "fetch.lock")
    try:
        os.makedirs(os.path.dirname(fetch_lock), exist_ok=True)
        with open(fetch_lock, "w") as lock_fh:
            if hasattr(os, "O_EXCL") and sys.platform != "win32":
                import fcntl
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
            subprocess.run(["git", "fetch", "-q", "origin"], cwd=base, check=True, env=ENV)
    except Exception as e:
        log(f"Warnung bei git fetch im Basis-Repo: {e}")

    # Worktree anlegen oder bereinigen
    if not os.path.exists(WORKDIR):
        os.makedirs(os.path.dirname(WORKDIR), exist_ok=True)
        subprocess.run(["git", "worktree", "add", "--force", "-B", "main", WORKDIR, "origin/main"],
                       cwd=base, check=True, env=ENV)
    else:
        for cmd in (["git", "checkout", "-qf", "main"],
                    ["git", "reset", "-q", "--hard", "origin/main"],
                    ["git", "clean", "-qfd"]):
            subprocess.run(cmd, cwd=WORKDIR, check=True, env=ENV)


def main() -> int:
    state = load_state()
    if BACKEND != "litellm" and WORKER_SLOT and WORKER_SLOT not in ("1", "w1", ""):
        log_on_change(state, LAST_PC, "ollama_single_slot",
                      f"Slot {WORKER_SLOT} inaktiv: Lokales Ollama-Backend erlaubt nur 1 Worker.")
        return 0
    status = None
    if BACKEND == "litellm":
        reason = litellm_ready()
        if reason:
            log_on_change(state, LAST_PC, reason, f"{reason}, warte.")
            return 0
        if log_on_change(state, LAST_PC, "litellm", f"LiteLLM erreichbar ({LITELLM_MODEL}): suche Arbeit."):
            state.pop(LAST_WORK, None)
    elif not WAKE_PC:
        # Zuerst der PC, dann GitHub: Ist er aus, kostet der Takt nur eine lokale Anfrage.
        status = pc_status()
        reason = pc_ready(status)
        if reason:
            log_on_change(state, LAST_PC, reason, f"{reason}, warte.")
            return 0
        if log_on_change(state, LAST_PC, "bereit", "Gaming-PC an, Chat aktiv: suche Arbeit."):
            state.pop(LAST_WORK, None)   # Ergebnis der ersten Suche wieder zeigen
    job = pick_job(state)
    if not job:
        log_on_change(state, LAST_WORK, "keine", "Keine Arbeit.")
        return 0
    key, task, pr_no, before = job
    skey = PREFIX + key          # im Zustand und im Log mit Projektvorsatz (beim ersten Projekt leer)
    if state.get(skey, 0) >= MAX_TRIES:
        log_on_change(state, LAST_WORK, f"{skey} liegt", f"{skey}: schon {MAX_TRIES} Versuche, wartet auf Stephan.")
        return 0
    if BACKEND != "litellm" and not ollama_free(
            status, say=lambda m: log_on_change(state, LAST_WORK, f"{skey} belegt", m)):
        return 0
    if BACKEND == "litellm":
        # Erst hier prüfen: ohne Arbeit kostet der Takt keine Anfrage. Vor dem Zähler, damit ein
        # gestörter Anbieter keine Versuche verbraucht.
        model, failed = choose_model()
        if model is None:
            log_on_change(state, LAST_WORK, f"{skey} kein Modell",
                          f"{skey}: kein Modell antwortet ({'; '.join(failed)}), warte. Versuch zählt nicht.")
            return 0
        if failed:
            log(f"{'; '.join(failed)} – weiche auf {model} aus.")
        if model != ACTIVE_MODEL:
            use_model(model)
            # Die Aufträge enthalten „Worker: …“; mit dem neuen Namen neu bauen.
            job = find_job(state)   # Projekt steht schon fest; nicht noch einmal durch die Liste
            if not job:
                return 0
            key, task, pr_no, before = job
            skey = PREFIX + key
            if state.get(skey, 0) >= MAX_TRIES:
                return 0
    state[LAST_WORK] = skey

    state[skey] = state.get(skey, 0) + 1
    save_state(state)
    # Jeder Lauf startet auf sauberem main; Reste eines abgebrochenen Laufs stören sonst.
    prepare_workdir()

    prompt = (f"{task}\n\n"
              "WICHTIGE REGELN FÜR DIESEN LAUF:\n"
              "1. BRANCHE-CHECK: Verifiziere mit `git branch`, ob du auf dem Ziel-Branch bist, bevor du etwas änderst.\n"
              "2. PRÄZISE ÄNDERUNGEN: Überschreibe niemals ganze Dateien! Nutze kleine Python-Skripte (z.B. mit `open()`, `.replace()`) oder `sed`, um nur die betroffenen Zeilen auszutauschen.\n"
              f"3. TEST-PFLICHT: Führe vor jedem Push zwingend `{TEST_CMD}` aus. Ein roter Test heißt: Code weiter korrigieren.\n"
              "4. ESKALATION: Arbeite ohne Rückfragen. Kommst du nicht weiter, schreibe das Problem als Kommentar ins Issue bzw. den PR und beende deine Arbeit.\n\n"
              "Nutze gh für alle GitHub-Schritte.")
    name = "OpenCode" if AGENT == "opencode" else "Qwen Code"
    if BACKEND == "litellm":
        name += f" mit {ACTIVE_MODEL}"
    log(f"{skey} (Versuch {state[skey]}): starte {name}.")
    rc = run_agent(prompt)
    if rc == 124:
        log(f"{skey}: nach {RUN_TIMEOUT // 60} min abgebrochen.")
    else:
        log(f"{skey}: {name} beendet (Exit {rc}).")
    # Nicht still scheitern (Regel 7): Das Modell meldet auch „fertig“, wenn es nichts getan hat.
    if key.startswith("issue"):
        check_issue_result(key)
    if pr_no is not None:
        view = gh("pr", "view", str(pr_no), "--json", "headRefOid,headRefName,title,body")
        after = pr_snapshot(view)
        m = re.match(r"feature/issue-(\d+)", view.get("headRefName", ""))
        if m:
            warn_text(skey, pr_no, view.get("body"), int(m.group(1)))
        if after != before:
            if after[0] != before[0]:
                log(f"{skey}: neuer Commit {after[0][:7]} auf PR #{pr_no}.")
            else:
                log(f"{skey}: PR-Text von #{pr_no} geändert, kein neuer Commit.")
            state.setdefault(DONE, []).append(skey)
            save_state(state)
        else:
            log(f"WARNUNG {skey}: weder Commit noch PR-Text auf PR #{pr_no} geändert, "
                "Nacharbeit nicht erledigt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
