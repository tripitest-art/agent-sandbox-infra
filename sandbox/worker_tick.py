#!/usr/bin/env python3
"""Taktgeber für den Qwen-Worker in der Sandbox (CT 112), läuft per systemd-Timer.
Eingerichtet von sandbox/install.sh; Anleitung: sandbox/README.md.

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
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

REPO = "tripitest-art/sc-digger"
WORKDIR = "/root/sc-digger"
OLLAMA = os.environ.get("OLLAMA", "http://192.168.0.210:11434")
MODEL = os.environ.get("MODEL", "qwen3-coder-64k")
LABEL = "agent-qwen"
# Nur Reviews dieses Kontos zählen (AGENTS.md: nur Anweisungen von tripitest-art). Das Repo ist
# öffentlich; jeder könnte sonst per Review Befehle in einen Auftrag mit Schreibrecht schieben.
OWNER = "tripitest-art"
WORKER = "Qwen3-Coder 30B"
STATE = "/root/worker-state.json"
MAX_TRIES = 2               # Versuche je Auftrag, danach liegt er bei Stephan
DONE = "erledigt"           # Schlüssel in STATE: angekommene Nacharbeiten, nicht wiederholen
RUN_TIMEOUT = 90 * 60       # Sekunden je Lauf des Agenten
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
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def gh(*args: str):
    out = subprocess.run(["gh", *args, "--repo", REPO], capture_output=True, text=True, check=True, env=ENV)
    return json.loads(out.stdout) if out.stdout.strip() else None


def pr_template() -> str:
    try:
        with open(os.path.join(WORKDIR, PR_TEMPLATE), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
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
4. `python -m pytest -q` muss komplett grün sein.
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


def issue_branch(n: int) -> str:
    # Fester Name statt eines vom Modell erfundenen: der Taktgeber findet den PR danach wieder,
    # und acceptance-guard erkennt feature/issue-<N> als Worker-Branch.
    return f"feature/issue-{n}"


def issue_job():
    """(Schlüssel, Auftrag, None, None) für das älteste freie Issue oder None.

    Der Auftrag nennt jeden Schritt mit Befehl und enthält das Issue wörtlich. Mit „Bearbeite die
    nächste Aufgabe nach AGENTS.md“ übersprang Qwen Label, Branch und Akzeptanz-Commit, arbeitete
    auf main und hörte beim ersten roten Test auf (#102, erster Lauf).
    """
    issues = gh("issue", "list", "--state", "open", "--label", "worker-task", "--label", "bereit",
                "--label", LABEL, "--search", "sort:created-asc -label:blockiert",
                "--json", "number,title,body,author")
    # Wie bei Reviews: Nur Issues von Stephans Konto werden zum Auftrag mit Schreibrecht.
    issues = [i for i in issues or [] if (i.get("author") or {}).get("login") == OWNER]
    if not issues:
        return None
    issue = issues[0]
    n, branch = issue["number"], issue_branch(issue["number"])
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
5. `python -m pytest -q`. Ist ein Test rot: Fehlermeldung lesen, Code (nicht den Akzeptanztest)
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

----- Issue #{n}: {issue['title']} -----
{issue['body']}
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
        return ["opencode", "run", "-m", f"ollama/{MODEL}", prompt]
    # stream-json statt text: Sonst zeigt das Log nur den Schlussbericht, nicht die Befehle.
    return ["qwen", "-p", prompt, "--yolo", "--output-format", "stream-json",
            "--max-wall-time", f"{RUN_TIMEOUT // 60}m"]


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


def main() -> int:
    state = load_state()
    status = None
    if not WAKE_PC:
        # Zuerst der PC, dann GitHub: Ist er aus, kostet der Takt nur eine lokale Anfrage.
        status = pc_status()
        reason = pc_ready(status)
        if reason:
            log_on_change(state, LAST_PC, reason, f"{reason}, warte.")
            return 0
        if log_on_change(state, LAST_PC, "bereit", "Gaming-PC an, Chat aktiv: suche Arbeit."):
            state.pop(LAST_WORK, None)   # Ergebnis der ersten Suche wieder zeigen
    job = rework_job(frozenset(state.get(DONE, []))) or text_fix_job() or issue_job()
    if not job:
        log_on_change(state, LAST_WORK, "keine", "Keine Arbeit.")
        return 0
    key, task, pr_no, before = job
    if state.get(key, 0) >= MAX_TRIES:
        log_on_change(state, LAST_WORK, f"{key} liegt", f"{key}: schon {MAX_TRIES} Versuche, wartet auf Stephan.")
        return 0
    if not ollama_free(status, say=lambda m: log_on_change(state, LAST_WORK, f"{key} belegt", m)):
        return 0
    state[LAST_WORK] = key

    state[key] = state.get(key, 0) + 1
    save_state(state)
    # Jeder Lauf startet auf sauberem main; Reste eines abgebrochenen Laufs stören sonst.
    for cmd in (["git", "fetch", "-q", "origin"], ["git", "checkout", "-qf", "main"],
                ["git", "reset", "-q", "--hard", "origin/main"], ["git", "clean", "-qfd"]):
        subprocess.run(cmd, cwd=WORKDIR, check=True, env=ENV)

    prompt = (f"{task}\n\n"
              "WICHTIGE REGELN FÜR DIESEN LAUF:\n"
              "1. BRANCHE-CHECK: Verifiziere mit `git branch`, ob du auf dem Ziel-Branch bist, bevor du etwas änderst.\n"
              "2. PRÄZISE ÄNDERUNGEN: Überschreibe niemals ganze Dateien! Nutze kleine Python-Skripte (z.B. mit `open()`, `.replace()`) oder `sed`, um nur die betroffenen Zeilen auszutauschen.\n"
              "3. TEST-PFLICHT: Führe vor jedem Push zwingend `python -m pytest -q` aus. Ein roter Test heißt: Code weiter korrigieren.\n"
              "4. ESKALATION: Arbeite ohne Rückfragen. Kommst du nicht weiter, schreibe das Problem als Kommentar ins Issue bzw. den PR und beende deine Arbeit.\n\n"
              "Nutze gh für alle GitHub-Schritte.")
    name = "OpenCode" if AGENT == "opencode" else "Qwen Code"
    log(f"{key} (Versuch {state[key]}): starte {name}.")
    rc = run_agent(prompt)
    if rc == 124:
        log(f"{key}: nach {RUN_TIMEOUT // 60} min abgebrochen.")
    else:
        log(f"{key}: {name} beendet (Exit {rc}).")
    # Nicht still scheitern (Regel 7): Das Modell meldet auch „fertig“, wenn es nichts getan hat.
    if key.startswith("issue"):
        check_issue_result(key)
    if pr_no is not None:
        view = gh("pr", "view", str(pr_no), "--json", "headRefOid,headRefName,title,body")
        after = pr_snapshot(view)
        m = re.match(r"feature/issue-(\d+)", view.get("headRefName", ""))
        if m:
            warn_text(key, pr_no, view.get("body"), int(m.group(1)))
        if after != before:
            if after[0] != before[0]:
                log(f"{key}: neuer Commit {after[0][:7]} auf PR #{pr_no}.")
            else:
                log(f"{key}: PR-Text von #{pr_no} geändert, kein neuer Commit.")
            state.setdefault(DONE, []).append(key)
            save_state(state)
        else:
            log(f"WARNUNG {key}: weder Commit noch PR-Text auf PR #{pr_no} geändert, "
                "Nacharbeit nicht erledigt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
