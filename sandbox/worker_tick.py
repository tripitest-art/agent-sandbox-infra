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
import inspect
import json
import os
try:                        # POSIX-Nutzerdatenbank für den Rechte-Drop des Agenten (#217)
    import pwd
except ImportError:         # Windows kennt kein pwd; dort läuft der Takt nicht
    pwd = None  # type: ignore[assignment]
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # projects.py liegt daneben
import agent_settings  # noqa: E402
import projects  # noqa: E402
import worker_frame  # noqa: E402  (#234) deterministischer Rahmen für Branch, Test und PR-Text

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
# Verzeichnis der Slot-Telemetrie (/run/worker-slots/slot-<n>.json). Legt der Takt selbst an;
# install.sh und die systemd-Units bleiben unangetastet (#14). qwen_webhook.py liest es.
SLOT_DIR: str = os.environ.get("SLOT_DIR", "/run/worker-slots")
OLLAMA = os.environ.get("OLLAMA", "http://192.168.0.210:11434")
MODEL = os.environ.get("MODEL", "qwen3-coder-64k")
LABEL = "agent-qwen"
# Nur Reviews dieses Kontos zählen (AGENTS.md: nur Anweisungen von tripitest-art). Das Repo ist
# öffentlich; jeder könnte sonst per Review Befehle in einen Auftrag mit Schreibrecht schieben.
OWNER = "tripitest-art"
BACKEND = os.environ.get("BACKEND", "ollama")   # oder "litellm"
LITELLM = os.environ.get("LITELLM", "http://192.168.0.211:4000")
LITELLM_MODEL = agent_settings.value("LITELLM_MODEL", os.environ.get("LITELLM_MODEL", "coder"))
# Ausweichmodelle (Aliase im LiteLLM-Proxy, der Key »worker« muss sie dürfen), durch Komma getrennt.
LITELLM_FALLBACKS = [m.strip() for m in os.environ.get("LITELLM_FALLBACKS", "coder-go,coder-or,coder-free").split(",") if m.strip()]
PROBE_TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "45"))   # Sekunden je Probe
# #180: Erst nach 20 Minuten ist ein LiteLLM-Ausfall eine Meldung wert; ein Proxy-Neustart
# oder ein kurzer Netzaussetzer bleibt still. Die Schlüssel liegen im Slot-Zustand, deshalb
# meldet nur der erste Slot.
LITELLM_ALARM_S = 1200           # 20 Minuten
LITELLM_AB, LITELLM_ALARM = "_litellm_ab", "_litellm_alarm"     # Schlüssel im Zustand
# Steht als „Worker: …“ im PR; die Review-Regel (andere Modellfamilie) hängt daran.
WORKER_NAMES = {
    "coder": "DeepSeek (LiteLLM coder)",
    "coder-go": "DeepSeek (LiteLLM coder-go, OpenCode Go)",
    "coder-pro": "DeepSeek V4 Pro (LiteLLM coder-pro)",
    "coder-pro-go": "DeepSeek V4 Pro (LiteLLM coder-pro-go, OpenCode Go)",
    "coder-kimi-go": "Kimi K2.7 Code (LiteLLM coder-kimi-go, OpenCode Go)",
    "coder-qwen-go": "Qwen 3.8 Max (LiteLLM coder-qwen-go, OpenCode Go)",
    "coder-mimo": "Xiaomi MiMo-V2.6-Flash (LiteLLM coder-mimo, OpenCode Go)",
    "reviewer-go": "GLM 5.3 Flash (LiteLLM reviewer-go, OpenCode Go)",
    "coder-fast": "DeepSeek (LiteLLM coder-fast, ohne Thinking)",
    "coder-or": "DeepSeek (LiteLLM coder-or, OpenRouter)",
    "coder-free": "Codestral (LiteLLM coder-free)",
    "coder-glm": "GLM 5.3 Flash (LiteLLM coder-glm)",
    "coder-qwen": "Qwen 2.5 Coder (LiteLLM coder-qwen)",
    "coder-nvidia": "Qwen 2.5 Coder (LiteLLM coder-nvidia, NVIDIA NIM)",
    "coder-nemotron-free": "Nemotron 3.5 Lightning (LiteLLM, OpenCode Free)",
    "coder-minimax-free": "MiniMax M3.1 Flash (LiteLLM, OpenCode Free)",
    "coder-beam-free": "Beam (LiteLLM, OpenCode Free)",
}

# Mehrstufige Modell-Eskalation über OpenCode Go:
# Stufe 1 (Versuch 1): DeepSeek V4.1 Flash (schnell, 0 € Token-Kosten)
# Stufe 2 (Versuch 2): DeepSeek V4 Pro (Reasoning / Denkmodell, 0 € Token-Kosten)
# Stufe 3 (Versuch 3+): Kimi K2.7 Code (Modellwechsel bei hartnäckigen Aufgaben, 0 € Token-Kosten)
ESCALATION_TIERS = {
    1: "coder-go",
    2: "coder-pro-go",
    3: "coder-kimi-go",
}


def model_for_attempt(attempt: int, base_model: str | None = None) -> str:
    """Wählt das Modell passend zur Eskalationsstufe über OpenCode Go (Versuch 1 -> Stufe 1, 2 -> Stufe 2, 3+ -> Stufe 3).
    Ist LITELLM_MODEL nicht coder-go, bleibt das eingestellte Basismodell fest."""
    base = LITELLM_MODEL if base_model is None else base_model
    if base != "coder-go":
        return base
    tier = min(max(1, attempt + 1), max(ESCALATION_TIERS.keys()))
    return ESCALATION_TIERS.get(tier, "coder-go")


def worker_name(model: str) -> str:
    return WORKER_NAMES.get(model, f"{model} (LiteLLM)")


WORKER = os.environ.get("WORKER", worker_name(LITELLM_MODEL) if BACKEND == "litellm" else "Qwen3-Coder 30B")
ACTIVE_MODEL = LITELLM_MODEL   # BACKEND=litellm: Alias dieses Laufs, nach der Probe ggf. ein Ausweichmodell
MAX_TRIES = agent_settings.value("MAX_TRIES", 2)   # Versuche je Auftrag, danach liegt er bei Stephan
DONE = "erledigt"           # Schlüssel in STATE: angekommene Nacharbeiten, nicht wiederholen
# Minuten je Lauf des Agenten. Über LiteLLM kürzer: das Modell ist schneller, und jede Minute
# kostet Token; 90 min hat bei #122 nur Zeit verbrannt.
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT_MIN", "30" if BACKEND == "litellm" else "90")) * 60
AGENT = os.environ.get("AGENT", "qwen-code")   # oder "opencode"
# #217: Der Agentenprozess läuft nicht als root, sondern als eigener unprivilegierter Nutzer.
# Damit kann das Modell über Shell-Aufrufe weder /etc/*.env (600 root) noch /root/.config/gh
# oder fremde Worktrees lesen. Den Nutzer legt sandbox/install.sh an.
AGENT_USER = os.environ.get("AGENT_USER", "worker")
PR_TEMPLATE = ".github/pull_request_template.md"
TOOL_OUTPUT_LINES = 15      # so viele Zeilen je Werkzeugausgabe ins Log
# Obergrenzen der Listenabfragen (#200): Ohne --limit fordert gh bis zu 30 Einträge an, bei
# verschachtelten Feldern (reviews, commits) kostet das viele GraphQL-Punkte. Mehr als 10 offene
# PRs bzw. 20 offene Issues je Sorte braucht ein Takt nicht; das stündliche Kontingent wird sonst
# vorzeitig verbraucht.
PR_LIST_LIMIT = 10          # GraphQL-Kontingent schonen: max. 10 offene PRs abfragen
ISSUE_LIST_LIMIT = 20       # max. 20 offene Issues je Abfrage
# Dynamisches Prioritäts-Scoring der Issue-Kandidaten (#240). Die Werte sind Punktzahlen, keine
# Sekunden: Ein Label `prio:kritisch` soll jede Alters- und Unblock-Punktzahl überwiegen, damit
# ein eiliger Bugfix nicht hinter alten Aufgaben verhungert.
PRIO_WEIGHTS: dict[str, float] = {
    "prio:kritisch": 1000.0,
    "prio:hoch": 300.0,
    "prio:normal": 100.0,
    "prio:niedrig": 0.0,
}
DEFAULT_PRIO_SCORE: float = 100.0
# Je blockierter Folgeaufgabe, die auf dieses Issue wartet: kräftiger Hebel für den Durchsatz.
UNBLOCK_WEIGHT: float = 150.0
# Anti-Starvation: linearer Bonus je Tag seit Erstellung, damit auch `prio:niedrig` irgendwann dran ist.
AGING_WEIGHT_PER_DAY: float = 5.0
BLOCKED_ISSUES_LIMIT: int = 30   # max. 30 offene `blockiert`-Issues für den Unblock-Hebel abfragen
# Sekunden, die der Takt aufs Wecken wartet; länger als WAKE_TIMEOUT des Proxys (180 s),
# damit der Proxy aufgibt und nicht der Takt.
WAKE_WAIT = int(os.environ.get("WAKE_WAIT", "240"))
# 1: bei Arbeit den Gaming-PC per WoL wecken. 0 (Vorgabe): nur arbeiten, wenn er an ist und
# Chat aktiv; dafür läuft der Takt oft (install.sh, TAKT).
WAKE_PC = os.environ.get("WAKE_PC", "0") == "1"
# Schlüssel in STATE: letzter Zustand von PC und Arbeit (Meldung nur beim Wechsel)
LAST_PC, LAST_WORK = "_pc", "_arbeit"

# Verzeichnis der gemeinsamen Auftrags-Belegung (#59): Zustand und Versuchszähler bleiben je Slot
# (`/root/worker-state-w<N>.json`), nur die Belegung gehört allen zusammen. Ohne sie nahmen beide
# Slots denselben Nacharbeits-Auftrag an (15.10.: sc-digger pr287 um 10:36 in w1 und w2).
CLAIM_DIR = os.environ.get("CLAIM_DIR", "/var/lib/worker-claims")
# Ab diesem Alter räumt cleanup_claims() auf (#170). Bewusst lang: Eine .done-Marke verhindert
# sonst, dass dieselbe Nacharbeit in einem anderen Slot erneut anläuft (#59).
CLAIM_MAX_AGE_DAYS = 30
# #183: Ein Issue mit `in-arbeit`, das so lange unverändert ist, ohne offenen PR auf seinem Branch
# und ohne lebende Belegung, steht still. Der Takt meldet das einmal, setzt aber kein Label zurück
# (Stephan entscheidet). 40 Minuten: länger als ein Lauf (`timeout -k 30 1860`).
VERWAIST_S = 2400
VERWAIST = "_verwaist:"      # Vorsatz der Schlüssel im Zustand: f"_verwaist:{nummer}"
# Verzeichnisse, für die claim_job schon eine „⚠ Stephan:“-Zeile geschrieben hat. Eigener Merker,
# weil claim_job keinen Zustand bekommt; der Takt ist ein kurzer Prozess je Auftrag.
_CLAIM_WARNED: set[str] = set()

# Verwaiste in-arbeit-Claims automatisch freigeben (#206 / Vorfall 2026-10-19):
# Ein Issue mit `in-arbeit` ohne Branch, ohne PR und ohne lebenden Slot gilt ab diesem
# Alter (Minuten) als verwaist.
ORPHAN_MINUTEN = 45

# Dauerkollision melden (#206): Ab dieser Dauer (Minuten) ununterbrochener Kollision
# mit demselben aktiven Auftrag genau einmal alarmieren. Gespeichert im Zustand (state),
# damit der Merker über die Takt-Prozesse hinweg erhalten bleibt.
KOLLISION_MELDUNG_MINUTEN = 60
KOLLISION = "_kollision:"  # Vorsatz im Zustand: f"_kollision:{cand_no}:{blocker_no}"

# Stillstand melden (#218): Findet der Takt keine Arbeit, obwohl etwas wartet (mindestens ein
# übersprungenes Issue oder ein geparkter Auftrag), merkt er den Beginn in `_stillstand`. Ab
# dieser Dauer (Minuten) ununterbrochener Untätigkeit meldet der erste Slot einmal.
STILLSTAND_MINUTEN = 60
STILLSTAND = "_stillstand"   # Schlüssel im Zustand: {"seit": <Epoche>, "gemeldet": bool}


def reset_kollision_merker(state: dict | None = None) -> None:
    """Setzt die Kollisionsmerker im Zustand zurück (vor allem für Tests)."""
    if state is None:
        state = load_state()
    for k in list(state.keys()):
        if k.startswith(KOLLISION):
            del state[k]
    save_state(state)


# Gründe der im letzten Takt von issue_job übersprungenen Kandidaten (#187). main() zeigt sie in
# der Slot-Telemetrie im Leerlauf, weil sie bisher nur im Log standen und den echten Grund der
# Untätigkeit verdeckten. issue_job leert die Liste zu Beginn jedes Aufrufs.
SKIPPED: list[str] = []

# Audit-Log (#85): pro Ereignis eine JSON-Zeile, maschinell auswertbar — anders als der
# Fließtext in /root/worker.log. audit() liest den Pfad bei jedem Aufruf aus diesem Modulglobal
# neu, damit Tests ihn belegen können, und legt die Datei bei Bedarf an.
AUDIT_LOG: str = os.environ.get("AUDIT_LOG", "/root/audit.jsonl")
# So viele Zeilen liest read_audit_tail() für run_overlap(): Beide Slots schreiben höchstens
# wenige Ereignisse je Takt, 400 Zeilen decken die offene Laufzeit sicher ab (#178).
AUDIT_TAIL_LINES = 400
# Feldnamen, deren Wert nie im Klartext ins Audit-Log geht (Regel 5); Groß-/Kleinschreibung egal.
_AUDIT_SECRET_KEYS = frozenset({"password", "secret", "api_key", "authorization", "token"})

# Wie .github/scripts/acceptance_guard.py: Kommentare der Vorlage zählen nicht.
_LINK_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)
_WORKER_LINE_RE = re.compile(r"^[\s>*_-]*Worker[*_]*\s*:[\s*_]*\w", re.I | re.M)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# Belegungsdateien werden nach dem Auftragsschlüssel benannt: alles außer A-Z a-z 0-9 . @ - wird „_“.
_CLAIM_UNSAFE_RE = re.compile(r"[^A-Za-z0-9.@-]")

# In beiden Aufträgen: Qwen hielt „GraphQL: …“ von gh für Erfolg (#103).
ERROR_RULE = (
    "Gibt ein Befehl eine Fehlermeldung aus (z. B. „GraphQL: …“, „error“, Exit-Code ungleich 0), ist\n"
    "der Schritt NICHT erledigt, auch wenn danach nichts mehr kommt. Beheben und wiederholen; geht\n"
    "das nicht, nicht „fertig“ melden, sondern den Fehler wörtlich als Kommentar {where} schreiben\n"
    "und aufhören.")

# Steht im Issue-Auftrag direkt nach Schritt 6 (#68). Der pre-push-Hook lehnt einen Push mit roten
# Tests ab; nur die Eskalation (Draft-PR nach drei Fehlschlägen, AGENTS.md Worker 6) darf ihn
# umgehen. Ohne diesen Satz hält der Agent den abgebrochenen Push für einen Fehler und weicht ihm
# aus, statt die Tests zu beheben.
PUSH_RULE = (
    "Der Push wird bei roten Tests abgelehnt (pre-push-Hook). Dann die Fehlermeldung lesen und "
    "beheben, nicht umgehen. Nur für die Eskalation mit Draft-PR (nach drei erfolglosen Anläufen, "
    "AGENTS.md Worker 6) gilt `git push --no-verify`.")

ENV = dict(os.environ, PATH=f"/root/venv/bin:/root/.opencode/bin:{os.environ.get('PATH', '/usr/bin:/bin')}")

# Regel 5: Diese Werte dürfen nie ins Log (#35) und, bis auf AGENT_ENV_PASS, nie in den Agentenprozess.
SECRET_ENV_KEYS = ("LITELLM_API_KEY", "LITELLM_REVIEW_KEY", "LITELLM_WORKER_KEY",
                   "LITELLM_PLANER_KEY", "GITHUB_TOKEN", "GH_TOKEN",
                   "HA_STATUS_URL", "HA_WEBHOOK_URL")

# Umgebung des Agentenprozesses: nur diese Schlüssel, soweit gesetzt. Die Liste darf
# erweitert werden, aber nie um einen Schlüssel aus SECRET_ENV_KEYS. PATH braucht der Agent
# (Aufruf von qwen/opencode/gh), HOME die gh-Konfiguration und die Modellkonfiguration.
AGENT_ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "SHELL",
                   "USER", "LOGNAME", "PWD", "PYTHONPATH", "XDG_CONFIG_HOME",
                   "XDG_DATA_HOME", "XDG_CACHE_HOME")


# Der Agent ruft das Modell selbst auf: qwen und opencode lesen den Schlüssel aus dieser Variablen
# (`envKey` in ~/.qwen/settings.json, `{env:LITELLM_API_KEY}` in der OpenCode-Konfiguration).
# Ohne ihn endet jeder Lauf nach 0 Sekunden mit `error_during_execution`. Er bleibt in
# SECRET_ENV_KEYS, damit `redact()` ihn aus jeder geloggten Zeile entfernt (Regel 5), und alle
# anderen Schlüssel aus SECRET_ENV_KEYS bleiben draußen.
AGENT_ENV_PASS = ("LITELLM_API_KEY",)


def agent_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Umgebung für den Agentenprozess: Schlüssel aus AGENT_ENV_ALLOW und AGENT_ENV_PASS, die gesetzt sind.

    `environ is None` liest `os.environ`. Schlüssel aus SECRET_ENV_KEYS kommen nie mit, auch
    wenn sie in `environ` stehen, außer den Namen in AGENT_ENV_PASS (der Modellschlüssel des
    Workers). Der Agent braucht PATH (Aufruf von qwen/opencode/gh) und
    HOME (gh-Konfiguration `~/.config/gh/hosts.yml`, Modellkonfiguration)."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items()
            if k in AGENT_ENV_PASS or (k in AGENT_ENV_ALLOW and k not in SECRET_ENV_KEYS)}


def redact(text: str, secrets: Iterable[str] | None = None) -> str:
    """Ersetzt jeden nicht-leeren Geheimniswert in `text` durch „***“ (Regel 5).

    `secrets is None` nimmt die Werte aller gesetzten SECRET_ENV_KEYS aus `os.environ`.
    Sehr kurze Werte (< 6 Zeichen) bleiben unangetastet, damit nicht jeder Buchstabe
    ersetzt wird. Wird auf jede Zeile angewendet, bevor sie ins Log geht."""
    if secrets is None:
        secrets = [os.environ.get(k, "") for k in SECRET_ENV_KEYS]
    for value in secrets:
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return text


def log(msg: str) -> None:
    prefix = f" [{SLOT_TAG}]" if SLOT_TAG else ""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}{prefix} {msg}", flush=True)


def audit(event: str, *, stderr_bei_fehler: bool = False, **fields) -> None:
    """Hängt eine JSON-Zeile an AUDIT_LOG: ts (UTC, ISO 8601), event, slot (WORKER_SLOT),
    model (MODEL), active_model und fields.

    model ist der Ollama-Name; active_model ist das tatsächlich genutzte Modell: ACTIVE_MODEL
    mit BACKEND=litellm (nach use_model() also ein Ausweichmodell), sonst MODEL (#147). Beide
    Globalen werden bei jedem Aufruf neu gelesen, damit ein Ausweichen sofort im Log steht.
    Das Feld steht vor fields und ist damit wie die übrigen Grundfelder überschreibbar.

    Nicht serialisierbare Werte gehen per `str()` hinein. Felder namens password, secret,
    api_key, authorization oder token erscheinen als „***“ (Regel 5), alle anderen unverändert.
    Der Pfad wird bei jedem Aufruf aus dem Modulglobal gelesen (#85). Wirft nie: scheitert das
    Schreiben, kommt eine Zeile „Audit-Log nicht schreibbar: <Fehlerklasse>“ auf stdout — ohne
    den Inhalt der Zeile, damit kein Geheimnis über die Fehlermeldung entweicht (#85).

    Mit `stderr_bei_fehler=True` geht diese Zeile nach stderr. alarm() nutzt das: stdout trägt
    dort die strikt einzeilige „⚠ Stephan:“-Meldung (#34), ein Schreibfehler darf sie nicht zu
    einer zweiten Zeile machen. systemd schreibt StandardError ebenfalls nach /root/worker.log,
    der Fehler bleibt also sichtbar."""
    try:
        zeile: dict[str, object] = {"ts": datetime.now(timezone.utc).isoformat(),
                                    "event": event, "slot": WORKER_SLOT, "model": MODEL,
                                    "active_model": ACTIVE_MODEL if BACKEND == "litellm" else MODEL}
        for name, value in fields.items():
            zeile[name] = "***" if name.lower() in _AUDIT_SECRET_KEYS else value
        text = json.dumps(zeile, ensure_ascii=False, default=str)   # erst serialisieren, dann öffnen
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception as e:
        meldung = f"Audit-Log nicht schreibbar: {type(e).__name__}"
        if not stderr_bei_fehler:
            log(meldung)
        else:
            prefix = f" [{SLOT_TAG}]" if SLOT_TAG else ""
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}{prefix} {meldung}",
                  file=sys.stderr, flush=True)


def spend_of(info) -> float | None:
    """`info["spend"]` als float, wenn `info` ein dict mit einer Zahl (kein bool) ist, sonst None.

    Reine Funktion. bool ist in Python eine Zahl, als Verbrauch aber unsinnig; ein fehlendes
    oder unlesbares spend lässt den Lauf offen, statt Kosten zu raten (#178)."""
    if not isinstance(info, Mapping):
        return None
    spend = info.get("spend")
    if isinstance(spend, bool) or not isinstance(spend, (int, float)):
        return None
    return float(spend)


def run_cost(vorher, nachher) -> float | None:
    """nachher - vorher, auf sechs Stellen gerundet.

    None, wenn einer der Werte keine Zahl (oder bool) ist oder nachher < vorher. Auf sechs
    Stellen, weil LiteLLM sechs Nachkommastellen liefert; ohne Rundung stünde dort
    0.19999999999999998 statt 0.2. Reine Funktion (#178)."""
    for wert in (vorher, nachher):
        if isinstance(wert, bool) or not isinstance(wert, (int, float)):
            return None
    if nachher < vorher:
        return None
    return round(float(nachher) - float(vorher), 6)


def read_audit_tail(limit: int = AUDIT_TAIL_LINES) -> list[dict]:
    """Die letzten `limit` Zeilen von AUDIT_LOG als dicts.

    Kaputte Zeilen und Nicht-Objekte werden übersprungen, eine fehlende oder unlesbare Datei
    ergibt []. Das Audit-Log ist Beiwerk: Sein Zustand darf den Takt nie aufhalten (#178)."""
    try:
        with open(AUDIT_LOG, encoding="utf-8") as fh:
            zeilen = fh.readlines()
    except OSError:
        return []
    ereignisse = []
    for zeile in zeilen[-limit:]:
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            wert = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(wert, dict):
            ereignisse.append(wert)
    return ereignisse


def _zeitpunkt(value) -> datetime | None:
    """ISO-8601-Text als datetime mit Zeitzone; None bei fehlendem oder kaputtem Wert.

    Ohne Zeitzone gilt UTC, damit ein Vergleich nie an naiven und bewussten Zeitpunkten scheitert."""
    if not isinstance(value, str):
        return None
    try:
        zeit = datetime.fromisoformat(value)
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


def run_overlap(events: list[dict], slot: str, start: str, ende: str) -> bool:
    """True, wenn ein anderer Slot im Zeitraum [start, ende] (ISO-8601) einen Lauf hatte.

    Nur run_started und run_finished zählen (review_posted nie). Ein Lauf reicht von seinem
    run_started bis zum nächsten run_finished desselben Slots; ohne run_finished läuft er noch.
    Die Grenzen sind einschließlich: Ein Lauf, der genau bei `start` endet oder genau bei `ende`
    beginnt, überlappt. Reine Funktion (#178)."""
    von, bis = _zeitpunkt(start), _zeitpunkt(ende)
    if von is None or bis is None:
        return False
    sortiert = []
    for ev in events:
        if not isinstance(ev, Mapping):
            continue
        zeit = _zeitpunkt(ev.get("ts"))
        if zeit is not None:
            sortiert.append((zeit, ev))
    offen: dict[str, datetime] = {}
    for zeit, ev in sorted(sortiert, key=lambda paar: paar[0]):
        art, eigener = ev.get("event"), str(ev.get("slot"))
        if art == "run_started":
            offen[eigener] = zeit
        elif art == "run_finished" and eigener in offen:
            anfang = offen.pop(eigener)
            if eigener != slot and anfang <= bis and zeit >= von:
                return True
    # Ohne run_finished läuft der Lauf noch: als Ende zählt unendlich.
    return any(anfang <= bis for anderer, anfang in offen.items() if anderer != slot)


def settle_pending(state: dict, info) -> bool:
    """Rechnet den offenen Lauf (state["_kosten_offen"]) ab, wenn spend_of(info) lesbar ist.

    Schreibt audit("run_cost", …) mit dem Unterschied vorher/nachher, `parallel=True`, wenn im
    Zeitraum ein anderer Slot lief (bei gemeinsamem Key steckt dessen Geld im Unterschied),
    entfernt den Eintrag, speichert den Zustand und gibt True. Ohne offenen Lauf oder ohne
    lesbaren Spend: False, nichts geschrieben, der Lauf bleibt offen (#178).

    Der Eintrag ohne `slot` (ältere Zustandsdateien) zählt als Lauf dieses Slots."""
    eintrag = state.get("_kosten_offen")
    if not isinstance(eintrag, dict):
        return False
    nachher = spend_of(info)
    if nachher is None:
        return False
    vorher = eintrag.get("spend_vorher")
    slot = eintrag.get("slot") or WORKER_SLOT
    start, jetzt = eintrag.get("start"), datetime.now(timezone.utc).isoformat()
    audit("run_cost", key=eintrag.get("key"), spend_vorher=vorher, spend_nachher=nachher,
          kosten=run_cost(vorher, nachher),
          parallel=run_overlap(read_audit_tail(), str(slot), str(start), jetzt),
          run_ende=eintrag.get("ende"))
    state.pop("_kosten_offen", None)
    save_state(state)
    return True


def alarm(skey: str, error: BaseException | str) -> None:
    """Eine Logzeile „⚠ Stephan: <skey>: <Klasse>: <Kurzbeschreibung>“ (Regel 7, HA-Automation).

    - Genau eine Zeile. Zeilenumbrüche in der Beschreibung werden zu Leerzeichen.
    - `<Klasse>` ist `type(error).__name__` bei einer Ausnahme, sonst "Fehler".
    - Der Text nach dem Zeitstempel ist höchstens 300 Zeichen lang; Längeres wird mit „…“
      abgeschnitten (der Zeitstempel kommt von `log()` davor, die Gesamtzeile bleibt <= 320).
    - `skey` ist der Zustandsschlüssel der laufenden Arbeit ("issue5",
      "pr103@<sha>", "text7@<sha>") oder "-", wenn der Fehler vor der Auftragswahl auftritt.
    - Übergeben wird nie eine rohe stderr-/Kommandoausgabe und nie ein Umgebungswert;
      die Kurzbeschreibung ist ein fester Text oder höchstens die erste Zeile der
      Exception-Meldung (Regel 5).

    Zusätzlich wird ein `stephan_alarm`-Event ins Audit-Log geschrieben (#222)."""
    if isinstance(error, BaseException):
        klass, detail = type(error).__name__, str(error)
    else:
        klass, detail = "Fehler", str(error)
    detail = " ".join(detail.split())          # Regel 7: daraus darf keine zweite Zeile werden
    prefix = f" [{SLOT_TAG}]" if SLOT_TAG else ""
    msg = f"⚠ Stephan: {skey}: {klass}: {detail}"
    limit = 300 - len(prefix) - 1              # Zeitstempel (19) + Vorsatz + Meldung <= 320
    if len(msg) > limit:
        msg = msg[:limit - 1] + "…"
    log(msg)
    # Audit-Log: strukturiertes stephan_alarm-Event (#222). Ein Schreibfehler geht nach stderr,
    # damit die einzeilige Meldung oben allein auf stdout bleibt (#34).
    audit("stephan_alarm", stderr_bei_fehler=True, skey=skey, error_class=klass, detail=detail)


def first_line(error: BaseException | str) -> str:
    """Nur die erste Zeile einer Meldung (Regel 5). So bekommt `alarm()` nie einen
    mehrzeiligen Text: die Auswahl der ersten Zeile ist Sache des Aufrufers, nicht der
    Normierung in `alarm()`."""
    lines = str(error).splitlines()
    return lines[0] if lines else ""


GH_GRUND_RATE_LIMIT = "GitHub-Rate-Limit erreicht"
GH_GRUND_TOKEN = "GitHub-Token ungültig oder abgelaufen"
GH_GRUND_NETZ = "GitHub nicht erreichbar"


def gh_fehler_grund(exc: BaseException) -> str:
    """Fester Klartext zur Ursache eines gescheiterten gh-Aufrufs, sonst ''.

    Nur für subprocess.CalledProcessError mit str-stderr; jede andere Ausnahme und stderr=None ergeben ''.
    Der stderr wird ohne Beachtung der Groß-/Kleinschreibung durchsucht, in dieser Reihenfolge:
    - 'rate limit' (auch 'secondary rate limit')                   -> GH_GRUND_RATE_LIMIT
    - 'http 401' oder 'bad credentials'                            -> 'GitHub-Token ungültig oder abgelaufen'
    - 'no such host', 'could not resolve host', 'connection refused',
      'timed out', 'timeout' oder 'tls handshake'                  -> 'GitHub nicht erreichbar'
    - sonst ''.
    Gibt nie Text aus stderr zurück (Regel 5: darin stehen Benutzerkennungen und Adressen). Reine Funktion."""
    if not isinstance(exc, subprocess.CalledProcessError):
        return ""
    stderr = getattr(exc, "stderr", None)
    if not isinstance(stderr, str):
        return ""
    text = stderr.lower()
    if "rate limit" in text:
        return GH_GRUND_RATE_LIMIT
    if "http 401" in text or "bad credentials" in text:
        return GH_GRUND_TOKEN
    if any(marker in text for marker in ("no such host", "could not resolve host", "connection refused",
                                         "timed out", "timeout", "tls handshake")):
        return GH_GRUND_NETZ
    return ""


def report_gh_error(state: dict, skey: str, exc: BaseException) -> bool:
    """Meldet einen gescheiterten Lauf. True, wenn eine Zeile geschrieben wurde.

    - Ohne Grund (gh_fehler_grund(exc) == ''): wie bisher alarm(skey, first_line(exc)); jedes Mal True;
      `state` bleibt unverändert.
    - Mit Grund: steht dieser schon unter state['_gherr'], wird nichts geschrieben (False). Sonst
      alarm(skey, f'{grund}: {first_line(exc)}'), state['_gherr'] = grund, True. Ein anderer Grund
      als der gemerkte wird also erneut gemeldet.
    Schreibt nicht selbst in die Zustandsdatei; das tut der Aufrufer."""
    grund = gh_fehler_grund(exc)
    if not grund:
        alarm(skey, first_line(exc))
        return True
    if state.get("_gherr") == grund:
        return False
    alarm(skey, f"{grund}: {first_line(exc)}")
    state["_gherr"] = grund
    return True


def clear_gh_error(state: dict) -> bool:
    """Löscht state['_gherr']. True, wenn der Schlüssel gesetzt war, sonst False."""
    return state.pop("_gherr", None) is not None


def normalize_slot(slot: str | None) -> str:
    """Slot-Name auf seine Ziffernfolge bringen; Vorgabe ist WORKER_SLOT oder „1“."""
    m = re.search(r"\d+", str(slot if slot is not None else WORKER_SLOT))
    return m.group(0) if m else "1"


def write_slot_status(status: str, *, slot: str | None = None, message: str | None = None,
                      issue: int | None = None, pr: int | None = None, title: str | None = None,
                      branch: str | None = None, model: str | None = None, agent: str | None = None,
                      started_at: str | None = None, pid: int | None = None,
                      project: str | None = None, slot_dir: str = SLOT_DIR) -> None:
    """Slot-Status atomar nach <slot_dir>/slot-<slot>.json schreiben (.tmp + os.replace).

    Die Datei ist die Quelle für Home Assistant (qwen_webhook.read_slots). Das Verzeichnis legt
    der Takt selbst an (#14), damit install.sh und die Units unverändert bleiben.
    """
    slot = normalize_slot(slot)
    data: dict[str, object] = {"slot": slot, "status": status,
                               "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    for key, value in (("message", message), ("issue", issue), ("pr", pr), ("title", title),
                       ("branch", branch), ("model", model), ("agent", agent),
                       ("started_at", started_at), ("pid", pid), ("project", project)):
        if value is not None:
            data[key] = value
    os.makedirs(slot_dir, exist_ok=True)
    path = os.path.join(slot_dir, f"slot-{slot}.json")
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def slot_status(status: str, **info) -> None:
    """Slot-Status schreiben, ohne den Takt anhalten zu können (Regel 7: nie still scheitern).

    Ein misslungenes Schreiben wird als Warnzeile sichtbar, bricht den Lauf aber nicht ab:
    Die Telemetrie ist Beiwerk, der Auftrag nicht. slot_dir aus dem Modul, damit Tests es belegen.
    """
    try:
        write_slot_status(status, slot_dir=SLOT_DIR, **info)
    except OSError as e:
        log(f"Warnung: Slot-Status nicht schreibbar ({type(e).__name__}: {e}).")


def use_project(p: "projects.Project", first: bool) -> None:
    """Schaltet die Globalen auf das Projekt `p` um (Repo, Arbeitsordner, Testbefehl, Hotspot)."""
    global REPO, PROJECT, PREFIX, TEST_CMD, HOTSPOT, HOTSPOT_LABEL, BASE_WORKDIR, WORKDIR
    REPO, PROJECT, TEST_CMD, HOTSPOT, HOTSPOT_LABEL = p.repo, p.name, p.test_cmd, p.hotspot, p.hotspot_label
    PREFIX = "" if first else f"{p.name}/"
    BASE_WORKDIR = p.workdir
    WORKDIR = f"{projects.worktrees_dir(p)}/w{WORKER_SLOT}" if WORKER_SLOT else p.workdir


def gh(*args: str, repo: str | None = None):
    """gh mit JSON-Ausgabe; `repo` wählt ein anderes Repository als das aktive Projekt (#187).

    Ohne `repo` gilt REPO (das Projekt, auf das use_project() zuletzt geschaltet hat). Die
    Projektvorsätze im Zustand (`<projekt>/prN@…`) brauchen die ausdrückliche Angabe, weil sie
    nicht zum gerade aktiven Projekt gehören müssen."""
    try:
        repo_args = [] if args and args[0] == "api" else ["--repo", repo or REPO]
        out = subprocess.run(["gh", *args, *repo_args], capture_output=True, text=True, check=True, env=ENV)
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 4 and "GH_TOKEN" in (getattr(exc, "stderr", None) or ""):
            return None
        raise
    text = out.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        # `gh issue edit` & Co. geben die URL aus, kein JSON. Der Befehl ist gelungen; wer eine
        # Liste erwartet, bekommt den Text und scheitert dort sichtbar. Früher brach hier das
        # Claiming ab, obwohl das Label schon gesetzt war (#52, #246, #247 blieben auf in-arbeit).
        return text


_TEMPLATE_CACHE: str | None = None      # Modulglobal; None = noch nicht geholt


def pr_template() -> str:
    """PR-Vorlage aus `origin/main`; einmal je Lauf geholt (Zwischenspeicher `_TEMPLATE_CACHE`).

    Beim Auswählen der Arbeit ist der Arbeitsbaum noch nicht aufgeräumt — `prepare_workdir()`
    läuft erst später in `main()`. Aus dem schmutzigen Baum ginge die Vorlage veraltet oder von
    einem abgebrochenen Lauf verändert als verbindliche „Regeln“ in den Auftrag ein (#37).
    Rückfall ist dieselbe Datei aus dem Arbeitsbaum, sonst "" — beides mit einer Warnung im Log
    statt eines stillen leeren Strings."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE
    for base in (WORKDIR, BASE_WORKDIR):
        try:
            out = subprocess.run(["git", "-C", base, "show", f"origin/main:{PR_TEMPLATE}"],
                                 capture_output=True, text=True, check=True, env=ENV)
        except (OSError, subprocess.CalledProcessError):
            continue
        if out.stdout.strip():
            _TEMPLATE_CACHE = out.stdout
            return _TEMPLATE_CACHE
    for base in (WORKDIR, BASE_WORKDIR):
        path = os.path.join(base, PR_TEMPLATE)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if text.strip():
            log(f"WARNUNG PR-Vorlage nicht aus origin/main, sondern aus dem Arbeitsbaum: {path}")
            _TEMPLATE_CACHE = text
            return _TEMPLATE_CACHE
    log("WARNUNG PR-Vorlage weder aus origin/main noch aus dem Arbeitsbaum lesbar; "
        "der Auftrag geht ohne Vorlage raus.")
    _TEMPLATE_CACHE = ""
    return _TEMPLATE_CACHE


def build_pr_body(template: str, issue_no: int, worker: str, summary: str) -> str:
    """Füllt die PR-Vorlage (Text von .github/pull_request_template.md).

    - Die Zeile "Closes #" (genau so, ohne Nummer) unter "## Was und warum" wird zu f"Closes #{issue_no}",
      gefolgt von einer Leerzeile und `summary` (getrimmt).
    - Die Kästchen "- [ ] `python -m pytest -q` grün" und "- [ ] Akzeptanztests aus dem Issue zeichengenau"
      (Zeilenanfang bis zum Wortlaut genügt) werden "- [x] …"; alle anderen Zeilen bleiben unverändert,
      auch die HTML-Kommentare der Vorlage.
    - Unter "## Offene Punkte / Abweichungen vom Issue" folgt nach der Überschrift und dem Kommentar die Zeile
      "keine".
    - Unter "## Umgesetzt von / Review durch" folgt nach der Überschrift und dem Kommentar die Zeile
      f"Worker: {worker}".
    Reine Funktion. Fehlt eine dieser Stellen in `template`, wird sie am Ende als eigener Abschnitt
    angehängt (Überschrift, Leerzeile, Inhalt), statt zu werfen."""
    body = (template or "").replace("\r\n", "\n")

    lines = []
    for line in body.splitlines():
        if line.startswith("- [ ] `python -m pytest -q` grün"):
            lines.append("- [x] " + line[len("- [ ] "):])
        elif line.startswith("- [ ] Akzeptanztests aus dem Issue zeichengenau"):
            lines.append("- [x] " + line[len("- [ ] "):])
        else:
            lines.append(line)
    body = "\n".join(lines)

    missing: list[tuple[str, str]] = []

    closes_content = f"Closes #{issue_no}\n\n{summary.strip()}"
    closes_re = re.compile(r"^Closes #$", re.M)
    if closes_re.search(body):
        body = closes_re.sub(closes_content, body, count=1)
    else:
        was_re = re.compile(r"^##\s*Was und warum\s*$", re.M)
        if was_re.search(body):
            body = was_re.sub(rf"## Was und warum\n\n{closes_content}", body, count=1)
        else:
            missing.append(("## Was und warum", closes_content))

    offene_re = re.compile(r"^##\s*Offene Punkte\s*/\s*Abweichungen vom Issue\s*$", re.M)
    m_offene = offene_re.search(body)
    if m_offene:
        start_pos = m_offene.end()
        comment_m = re.compile(r"\s*<!--.*?-->", re.S).match(body, start_pos)
        insert_pos = comment_m.end() if comment_m else start_pos
        body = body[:insert_pos] + "\n\nkeine\n" + body[insert_pos:]
    else:
        missing.append(("## Offene Punkte / Abweichungen vom Issue", "keine"))

    umgesetzt_re = re.compile(r"^##\s*Umgesetzt von\s*/\s*Review durch\s*$", re.M)
    m_umgesetzt = umgesetzt_re.search(body)
    worker_line = f"Worker: {worker}"
    if m_umgesetzt:
        start_pos = m_umgesetzt.end()
        comment_m = re.compile(r"\s*<!--.*?-->", re.S).match(body, start_pos)
        insert_pos = comment_m.end() if comment_m else start_pos
        body = body[:insert_pos] + f"\n\n{worker_line}\n" + body[insert_pos:]
    else:
        missing.append(("## Umgesetzt von / Review durch", worker_line))

    for heading, content in missing:
        body = body.rstrip() + f"\n\n{heading}\n\n{content}\n"

    return body


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


def verify_rework_pr(pr_no: int, expected_head_oid: str) -> tuple[bool, str]:
    """Prüft via gh pr view, ob der PR noch OPEN ist und HEAD unverändert ist.

    Zwischen der Auswahl der Nacharbeit und dem Agentenstart liegt die Modellprobe
    (`choose_model`, bis `PROBE_TIMEOUT`). Wird der PR in dieser Zeit gemergt oder geschlossen,
    wäre der Lauf verbrannte Zeit; ein fremder Push macht den Stand veraltet (#215).
    Gibt (True, "") zurück, wenn OK, sonst (False, 'Abbruchgrund').

    Wie `job_still_open()` ist eine unklare Lage kein Abbruchgrund: `--json state,headRefOid`
    liefert beides, und eine gescheiterte oder unvollständige Auskunft darf eine fällige
    Nacharbeit nicht dauerhaft blockieren (Regel 7).
    """
    try:
        view = gh("pr", "view", str(pr_no), "--json", "state,headRefOid")
    except Exception as e:
        log(f"Warnung: PR #{pr_no} nicht prüfbar ({type(e).__name__}), Nacharbeit läuft.")
        return True, ""
    if not isinstance(view, dict) or "state" not in view:
        return True, ""
    if view.get("state") != "OPEN":
        return False, f"PR #{pr_no} ist nicht mehr OPEN ({view.get('state')})"
    head = view.get("headRefOid") or ""
    if head != expected_head_oid:
        return False, (f"HEAD hat sich geändert ({head[:7] or 'unbekannt'} "
                       f"statt {expected_head_oid[:7]})")
    return True, ""


def rework_job(done=frozenset(), skip: Callable[[str], bool] | None = None):
    """(Schlüssel, Auftrag, PR-Nummer, pr_snapshot) für die älteste fällige Nacharbeit oder None.

    `done`: Schlüssel schon angekommener Nacharbeiten. Betraf sie nur den PR-Text, bleibt der
    Commit gleich und das Review sähe weiter fällig aus; ohne diese Liste lief #103 erneut.

    Das Review steht wörtlich im Auftrag: Qwen hat es sonst nicht gelesen, nur die Tests auf
    main laufen lassen und den PR selbst für fertig erklärt (#103, erster Lauf).
    """
    prs = gh("pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT), "--json",
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
        if skip and skip(key):
            continue   # geparkt: der nächste Kandidat kommt dran, der Takt läuft weiter
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


def text_fix_job(skip: Callable[[str], bool] | None = None):
    """(Schlüssel, Auftrag, PR-Nummer, pr_snapshot) für den ältesten PR mit unvollständigem Text.

    Der Schlüssel hängt am Head-Commit: Neue Commits geben einen neuen Anlauf, sonst gilt
    MAX_TRIES wie für jeden Auftrag.
    """
    prs = gh("pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT), "--json",
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
        key = f"text{n}@{pr['headRefOid'][:7]}"
        if skip and skip(key):
            continue   # geparkt: der nächste Kandidat kommt dran, der Takt läuft weiter
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


# Überschrift „Betroffene Dateien“ tolerant: jede Ebene ab „##“, Groß-/Kleinschreibung egal,
# Zusatz hinter dem Titel erlaubt. Ein handschriftlicher Titel ließ die Dateiliste früher leer,
# und Regel 8 fiel offen aus (#32).
_FILES_HEADING_RE = re.compile(r"^(#{2,6})\s*betroffene\s+dateien\b", re.I)
_ACCEPTANCE_PATH_RE = re.compile(r"^tests/acceptance/test_issue_\d+\.py$")
# Ein Kandidat ist ein Pfad, wenn er nur Pfadzeichen enthält: keine Leerzeichen, kein „=“, kein „:“.
_PATH_TOKEN_RE = re.compile(r"[\w./@+~-]+")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_BACKTICK_RE = re.compile(r"`([^`\n]*)`")
# Ausschlusssätze („… bleibt unberührt“, „Keine andere Datei …“) beschreiben, was der Worker
# NICHT anfassen darf. Ihre Pfade zählen nicht als belegt, sonst überspringt Regel 8 fremde
# Issues, die genau diese Dateien wirklich ändern (#79).
_EXCLUSION_WORDS = ("unberührt", "nicht angefasst", "nicht geändert")
_EXCLUSION_START = "keine andere datei"


def affected_files_section(md: str) -> str | None:
    """Sektion „Betroffene Dateien“ roh, oder None, wenn die Überschrift fehlt.

    Die Sektion endet an der nächsten Überschrift gleicher oder höherer Ebene.
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    start = level = None
    for i, line in enumerate(lines):
        m = _FILES_HEADING_RE.match(line.strip())
        if m:
            start, level = i + 1, len(m.group(1))
            break
    if start is None:
        return None
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s.startswith("#"):
            l = len(s) - len(s.lstrip("#"))
            if l <= level:
                return "\n".join(lines[start:i])
    return "\n".join(lines[start:])


def _path_candidate(text: str) -> str | None:
    """Token als Pfad, wenn es keine Leerzeichen und kein „=“ enthält; Verzeichnis ohne „/“."""
    p = text.strip()
    if not _PATH_TOKEN_RE.fullmatch(p):
        return None
    return p.rstrip("/")


# Regel 8: Fast jedes Issue nennt die Doku-Dateien (Pflicht nach AGENTS.md, „Doku und Changelog“)
# und den Akzeptanztest-Platzhalter `tests/acceptance/test_issue_<N>.py`. Beides blockierte bisher
# jeden zweiten Auftrag (#189); Code-Dateien zählen weiterhin.
COLLISION_EXEMPT: frozenset[str] = frozenset({"CHANGELOG.md", "README.md"})
_ACCEPTANCE_TEST_PREFIX = "tests/acceptance/test_issue_"


def collision_files(files: set[str]) -> set[str]:
    """Dateien, die für Regel 8 (Datei-Kollision) zählen: `files` ohne COLLISION_EXEMPT und ohne
    Pfade, die unter `tests/acceptance/` liegen und mit `test_issue_` beginnen (der Dateiname
    trägt im Issue-Text oft den Platzhalter `<N>`). Gibt eine neue Menge zurück; `files` bleibt
    unverändert."""
    return {
        f for f in files
        if f not in COLLISION_EXEMPT and not f.startswith(_ACCEPTANCE_TEST_PREFIX)
    }


def parse_affected_files(issue_body: str) -> set[str] | None:
    """Dateipfade aus der Sektion „Betroffene Dateien“; None, wenn die Sektion fehlt oder leer ist.

    None heißt „unbekannt“ und wird von den Aufrufern als Kollision gewertet (Regel 8 fällt
    nicht mehr offen aus). Ein Aufrufer, der nur den Hotspot prüfen will, benutzt
    `parse_affected_files(body) or set()` und stürzt damit nicht ab.

    Die Überschrift wird tolerant gesucht: jede Ebene ab „##“, Groß-/Kleinschreibung egal,
    Zusatz hinter dem Titel erlaubt („### Betroffene Dateien (nur diese)“). Die Sektion endet
    an der nächsten Überschrift gleicher oder höherer Ebene.

    Erkannt werden Pfade in Backticks und in Aufzählungen, auch ohne Endung (`Dockerfile`,
    `Makefile`) und als Verzeichnis (`sandbox/` -> "sandbox", ohne abschließenden Schrägstrich).
    Ein Kandidat ist nur dann ein Pfad, wenn er keine Leerzeichen und kein „=“ enthält:
    `- `irgendeine_variable = True` (keine Datei)` bleibt ausgeschlossen.
    `tests/acceptance/test_issue_<N>.py` steht weiterhin nie in der Menge. Die bestehende
    Zusicherung aus `tests/test_worker_tick.py::test_parse_affected_files` gilt unverändert.

    Ausschlusssätze zählen nicht (#79): In einem Absatz (Zeilen bis zur nächsten Leerzeile oder
    Aufzählungszeile), der irgendwo „unberührt“, „nicht angefasst“, „nicht geändert“ oder am
    Zeilenanfang „Keine andere Datei“ enthält, zählen alle Pfade nicht; ebenso in einer
    Aufzählungszeile mit einem dieser Wörter. Planer schreiben so, was der Worker NICHT anfassen
    darf. Absätze und Zeilen ohne diese Wörter verhalten sich wie bisher. Bleibt nichts übrig, ist
    das Ergebnis die leere Menge — nicht None, denn die Sektion existiert.
    """
    sec = affected_files_section(issue_body)
    if sec is None or not sec.strip():
        return None

    def _exclusion(text: str) -> bool:
        """Ausschlusssatz: Ausschlusswort irgendwo oder „Keine andere Datei“ am Zeilenanfang."""
        low = text.lower()
        if any(w in low for w in _EXCLUSION_WORDS):
            return True
        return any(line.lstrip().startswith(_EXCLUSION_START) for line in low.splitlines())

    def _excluded_lines(lines: list[str]) -> set[int]:
        """Zeilennummern, die zu einem Ausschlussabsatz oder einer Ausschlusszeile gehören."""
        excluded: set[int] = set()
        i, n = 0, len(lines)
        while i < n:
            if not lines[i].strip():
                i += 1
                continue
            if _BULLET_RE.match(lines[i]):
                if _exclusion(lines[i]):
                    excluded.add(i)
                i += 1
                continue
            j = i
            while j < n and lines[j].strip() and not _BULLET_RE.match(lines[j]):
                j += 1
            if _exclusion("\n".join(lines[i:j])):
                excluded.update(range(i, j))
            i = j
        return excluded

    lines = sec.split("\n")
    excluded = _excluded_lines(lines)
    files: set[str] = set()
    for idx, line in enumerate(lines):
        if idx in excluded:
            continue
        for m in _BACKTICK_RE.finditer(line):
            p = _path_candidate(m.group(1))
            if p and not _ACCEPTANCE_PATH_RE.match(p):
                files.add(p)
    for idx, line in enumerate(lines):
        if idx in excluded:
            continue
        m = _BULLET_RE.match(line)
        if not m:
            continue
        rest = _BACKTICK_RE.sub("", m.group(1)).lstrip()
        token = _PATH_TOKEN_RE.match(rest)
        if not token:
            continue
        p = _path_candidate(token.group(0))
        if p and not _ACCEPTANCE_PATH_RE.match(p):
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
    # Unbekannte Dateiliste (None) darf nicht abstürzen und zählt nicht als Hotspot-Treffer (#32).
    return HOTSPOT in (parse_affected_files(body) or set())


def active_in_progress_issues() -> list[dict] | None:
    """Offene Issues mit Label `in-arbeit` von Stephans Konto.

    None, wenn die Abfrage scheitert (Netz, Rate-Limit, gh-Fehler); die Ursache steht als
    Warnung im Log, aber ohne Geheimnisse. Eine leere Liste heißt dagegen belegt „kein
    aktiver Auftrag“. Der Aufrufer wertet None als unbekannt und baut keinen Auftrag.
    """
    try:
        active = gh("issue", "list", "--state", "open", "--label", "in-arbeit",
                    "--limit", str(ISSUE_LIST_LIMIT),
                    "--json", "number,title,body,author,labels,updatedAt")
        return [i for i in active or [] if (i.get("author") or {}).get("login") == OWNER]
    except Exception as e:
        # Nicht still scheitern: Früher lieferte dieser Zweig [], und ein zweiter Agent startete
        # in denselben Dateien (Regel 8 fiel offen aus, #32).
        log(f"Warnung: Abfrage aktiver Issues fehlgeschlagen ({type(e).__name__}: {e}); "
            "Lage unbekannt, kein Auftrag.")
        return None


def open_prs_for_issue_check() -> list[dict] | None:
    """Holt offene PRs des Repositories mit number und headRefName.

    None bei Fehlern (Netz, Rate-Limit, gh-Fehler) oder wenn die Abfrage >= PR_LIST_LIMIT
    Einträge liefert (weitere PRs möglich -> Lage unbekannt); nichts freigeben.
    """
    try:
        prs = gh("pr", "list", "--state", "open", "--limit", str(PR_LIST_LIMIT),
                 "--json", "number,headRefName")
        if not isinstance(prs, list):
            return None
        if len(prs) >= PR_LIST_LIMIT:
            log(f"Warnung: Abfrage offener PRs erreicht Limit ({PR_LIST_LIMIT}); "
                "weitere PRs möglich, Lage unbekannt, nichts freigeben.")
            return None
        return prs
    except Exception as e:
        log(f"Warnung: Abfrage offener PRs fehlgeschlagen ({type(e).__name__}: {e}); Lage unbekannt, nichts freigeben.")
        return None


def issue_label_age_minuten(n: int, issue: dict | None = None, now: float | None = None) -> float | None:
    """Alter des Labels `in-arbeit` eines Issues in Minuten.

    Liest das Alter direkt aus updatedAt des Issues aus GitHub (gh issue list).
    None, wenn das Alter nicht ermittelbar ist (Lage unbekannt).
    """
    if now is None:
        now = jetzt_s()
    # Schneller Pfad: vorab belegtes labeled_at im Dict (z. B. in Tests)
    if isinstance(issue, dict) and issue.get("labeled_at"):
        ts = _iso_sekunden(issue.get("labeled_at"))
        if ts is not None:
            return max(0.0, (now - ts) / 60.0)

    # Direkt aus updatedAt des Issues (aus gh issue list --json ... updatedAt)
    if isinstance(issue, dict) and issue.get("updatedAt"):
        ts = _iso_sekunden(issue.get("updatedAt"))
        if ts is not None:
            return max(0.0, (now - ts) / 60.0)

    log(f"Warnung: Alter von Issue #{n} nicht abfragbar; Lage unbekannt, nichts freigeben.")
    return None


def issue_has_active_slot(n: int) -> bool:
    """Prüft, ob ein Slot (Auftrags-Belegung/CLAIM_DIR) gerade an diesem Issue arbeitet."""
    skey = PREFIX + f"issue{n}"
    besitzer = _claim_owner(claim_path(skey))
    return besitzer is not None and _pid_alive(besitzer)


def pr_matches_issue(pr: dict, n: int) -> bool:
    """Prüft, ob ein offener PR mit Branch issue_branch(n) oder feature/issue-n-* existiert."""
    if not isinstance(pr, dict):
        return False
    target = issue_branch(n)
    branch = pr.get("headRefName") or ""
    return branch == target or branch.startswith(f"{target}-")


def release_orphan_claim(n: int, alter_minuten: int) -> bool:
    """Setzt das Label eines verwaisten Claims zurück (in-arbeit -> bereit).

    Schreibt eine „⚠ Stephan:“-Zeile über alarm() und ein audit()-Ereignis orphan_claim_released.
    True bei Erfolg (Label erfolgreich geändert), False bei Fehler.
    """
    try:
        # Idempotenz-Schutz vor doppelter Freigabe durch zwei parallele Slots im selben Takt
        view = gh("issue", "view", str(n), "--json", "labels")
        if isinstance(view, dict) and "labels" in view:
            labels = {lab.get("name") for lab in view["labels"] if isinstance(lab, dict)}
            if "in-arbeit" not in labels:
                return True
        gh("issue", "edit", str(n), "--add-label", "bereit", "--remove-label", "in-arbeit")
        log(f"Verwaister Claim von Issue #{n} ({alter_minuten} min) erfolgreich auf 'bereit' zurückgesetzt.")
        alarm(f"issue{n}", f"Verwaister Claim #{n} ({alter_minuten} min) freigegeben (in-arbeit -> bereit)")
        audit("orphan_claim_released", issue=n, age_minutes=alter_minuten)
        return True
    except Exception as e:
        detail = ((getattr(e, "stderr", None) or "").strip().splitlines() or [""])[0][:200]
        log(f"Freigabe von verwaistem Issue #{n} fehlgeschlagen: {e}" + (f" ({detail})" if detail else ""))
        return False


def cleanup_orphan_claims(active: list[dict], now: float | None = None) -> list[dict]:
    """Prüft aktive Aufträge (in-arbeit von OWNER) auf verwaiste Claims und gibt sie frei.

    Ein offenes Issue gilt als verwaist, wenn alle drei Bedingungen zutreffen:
    - kein offener PR mit Branch issue_branch(N) (ein Branch auf GitHub zählt nicht!),
    - das Issue (updatedAt) ist länger als ORPHAN_MINUTEN unverändert,
    - keine lebende Belegung hält den Auftrag (CLAIM_DIR, Schlüssel PREFIX + f"issue{N}").

    Ein verwaister Claim wird auf 'bereit' zurückgesetzt, alarmiert Stephan via alarm()
    und erzeugt ein audit()-Ereignis 'orphan_claim_released'.
    Er zählt nicht mehr zu den aktiven Aufträgen und wird aus der Rückgabeliste entfernt.
    Scheitert eine GitHub-Abfrage (PRs/Alter), bleibt die Lage unbekannt,
    nichts wird freigegeben und eine Warnung wird geloggt.
    """
    if not active:
        return active
    if now is None:
        now = jetzt_s()

    open_prs = open_prs_for_issue_check()
    if open_prs is None:
        return active

    remaining: list[dict] = []
    for a in active:
        if not isinstance(a, dict):
            continue
        # Fremdes Issue nie anfassen
        if (a.get("author") or {}).get("login") != OWNER:
            remaining.append(a)
            continue
        try:
            n = int(a.get("number"))
        except (TypeError, ValueError):
            remaining.append(a)
            continue

        # 1. Offener PR mit Branch issue_branch(n) vorhanden? (Ein Branch auf GitHub zählt nicht)
        if any(pr_matches_issue(pr, n) for pr in open_prs):
            remaining.append(a)
            continue

        # 2. Aktive lebende Belegung in CLAIM_DIR vorhanden?
        if issue_has_active_slot(n):
            remaining.append(a)
            continue

        # 3. Alter aus GitHub (updatedAt) >= ORPHAN_MINUTEN?
        age = issue_label_age_minuten(n, a, now=now)
        if age is None or age < ORPHAN_MINUTEN:
            remaining.append(a)
            continue

        # Alle Bedingungen erfüllt: verwaister Claim freigeben
        if release_orphan_claim(n, int(age)):
            # Erfolgreich freigegeben -> zählt nicht mehr zu active_files
            continue
        remaining.append(a)

    return remaining


def _track_kollision(cand_no: int, blocker_no: int | None, now: float,
                     current_collisions: set[tuple[int, int]],
                     state: dict) -> bool:
    """Verfolgt eine Datei-Kollision im Zustand und meldet Dauerkollision ab KOLLISION_MELDUNG_MINUTEN genau einmal.

    Gibt True zurück, wenn state geändert wurde.
    """
    if blocker_no is None:
        return False
    key = (cand_no, blocker_no)
    current_collisions.add(key)
    state_key = f"{KOLLISION}{cand_no}:{blocker_no}"
    geaendert = False
    if state_key not in state or not isinstance(state[state_key], dict):
        state[state_key] = {"start": now, "warned": False}
        geaendert = True

    entry = state[state_key]
    dauer = now - entry.get("start", now)
    if dauer >= KOLLISION_MELDUNG_MINUTEN * 60:
        if not entry.get("warned"):
            entry["warned"] = True
            geaendert = True
            minuten = int(dauer // 60)
            alarm(f"issue{cand_no}",
                  f"Dauerkollision seit {minuten} Minuten mit aktiver Aufgabe #{blocker_no} (Regel 8)")
    return geaendert


def claim_issue(n: int) -> bool:
    """Wechselt die Labels `bereit` → `in-arbeit`.

    True bei Erfolg; dann protokolliert audit() das Ereignis `issue_claimed` (#85). False, wenn
    der gh-Aufruf scheitert (Grund als erste stderr-Zeile im Log); in diesem Fall bleibt das
    Issue unverändert und es wird nichts ins Audit-Log geschrieben.
    """
    try:
        gh("issue", "edit", str(n), "--add-label", "in-arbeit", "--remove-label", "bereit")
        log(f"Issue #{n} erfolgreich auf 'in-arbeit' gesetzt.")
        # Erst nach dem erfolgreichen gh-Aufruf ins Audit-Log (#85); scheitert er, gibt es
        # nichts zu protokollieren (das Issue bleibt unverändert).
        audit("issue_claimed", issue=n)
        return True
    except Exception as e:
        # Bei einem gescheiterten gh-Aufruf steht der Grund in stderr, nicht in der Exception.
        detail = ((getattr(e, "stderr", None) or "").strip().splitlines() or [""])[0][:200]
        log(f"Claiming von Issue #{n} fehlgeschlagen: {e}" + (f" ({detail})" if detail else ""))
        return False


def job_issue_number(key: str) -> int | None:
    """Issue-Nummer aus einem Auftragsschlüssel „issue<N>“, sonst None.

    Der Taktgeber leitet daraus ab, welches Issue er vor dem Lauf übernehmen muss;
    Nacharbeiten („pr<N>@<Review-Zeit>“) und Text-Aufträge („text<N>@<Commit>“) liefern None.
    Es zählt der unpräfixte Schlüssel (ohne Projektvorsatz PREFIX).
    """
    m = re.fullmatch(r"issue(\d+)", key)
    return int(m.group(1)) if m else None


def issue_branch(n: int) -> str:
    # Fester Name statt eines vom Modell erfundenen: der Taktgeber findet den PR danach wieder,
    # und acceptance-guard erkennt feature/issue-<N> als Worker-Branch.
    return f"feature/issue-{n}"


_ACCEPTANCE_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_ACCEPTANCE_FENCE_RE = re.compile(r"^(`{3,}|~{3,})[^\n]*\n(.*?)\n?^\1[ \t]*$", re.M | re.S)


def _normalize_acceptance_code(code: str) -> str:
    """Zeilenenden und Leerraum am Zeilen-/Dateiende sind keine inhaltliche Änderung."""
    lines = [ln.rstrip() for ln in code.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip("\n")


def acceptance_block(issue_body: str | None) -> str | None:
    """Code-Block unter der Überschrift „Akzeptanztests“ oder None.

    Für jeden Text (auch None und leer) dasselbe Ergebnis wie
    extract_acceptance_block in .github/scripts/acceptance_guard.py. Der Takt kann diese Datei nicht
    importieren (Deploy-Klon und Projekt-Klone liegen getrennt), also steht die Regel hier noch einmal;
    ein Test vergleicht beide."""
    lines = (issue_body or "").replace("\r\n", "\n").split("\n")
    start = level = None
    for i, line in enumerate(lines):
        m = _ACCEPTANCE_HEADING_RE.match(line)
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
        m = None if in_fence else _ACCEPTANCE_HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= level:
            end = i
            break
    section = "\n".join(lines[start:end])
    fence = _ACCEPTANCE_FENCE_RE.search(section)
    if not fence:
        return None
    code = _normalize_acceptance_code(fence.group(2))
    return code or None


def prepare_issue_branch(n: int) -> bool:
    """Legt in WORKDIR den Branch issue_branch(n) an und sichert den Akzeptanztest als eigenen Commit.

    Holt das Issue mit gh("issue", "view", str(n), "--json", "body") (Feld "body"); liefert
    acceptance_block(...) None, ist das Ergebnis False und es lief kein git-Befehl.

    Sonst, jeweils mit cwd=WORKDIR, env=ENV, check=True (nur die zwei rev-parse mit check=False),
    in genau dieser Reihenfolge:
      1. ["git", "fetch", "-q", "origin"]
      2. ["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"]
      3. nur wenn 2. Exit != 0: ["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"]
      4. Checkout, je nach Fund:
         - Zweig auf origin vorhanden: ["git", "checkout", "-q", "-B", branch, f"origin/{branch}"]
         - sonst lokaler Zweig vorhanden: ["git", "checkout", "-q", branch]
         - sonst: ["git", "checkout", "-q", "-B", branch, "origin/main"]
      5. Die Datei tests/acceptance/test_issue_<n>.py (Ordner wird bei Bedarf angelegt) bekommt als Inhalt
         den Block plus ein abschließendes "\\n" (UTF-8, Zeilenenden LF, auch unter Windows). Hat sie diesen
         Inhalt Byte für Byte schon, folgt nichts mehr.
         Sonst: ["git", "add", "tests/acceptance/test_issue_<n>.py"], dann
         ["git", "commit", "-q", "-m", f"Akzeptanztests aus #{n}"].
    Ergebnis True. branch ist issue_branch(n). Ein Fehler eines Befehls (CalledProcessError) wird nicht
    gefangen; main() meldet ihn wie jeden anderen Fehler."""
    data = gh("issue", "view", str(n), "--json", "body")
    body = (data or {}).get("body") if isinstance(data, dict) else None
    block = acceptance_block(body)
    if block is None:
        return False

    branch = issue_branch(n)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=WORKDIR, env=ENV, check=True)
    res_remote = subprocess.run(
        ["git", "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"],
        cwd=WORKDIR,
        env=ENV,
        check=False,
    )
    if res_remote.returncode == 0:
        subprocess.run(["git", "checkout", "-q", "-B", branch, f"origin/{branch}"],
                       cwd=WORKDIR, env=ENV, check=True)
    else:
        res_local = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
            cwd=WORKDIR,
            env=ENV,
            check=False,
        )
        if res_local.returncode == 0:
            subprocess.run(["git", "checkout", "-q", branch],
                           cwd=WORKDIR, env=ENV, check=True)
        else:
            subprocess.run(["git", "checkout", "-q", "-B", branch, "origin/main"],
                           cwd=WORKDIR, env=ENV, check=True)

    datei_rel = f"tests/acceptance/test_issue_{n}.py"
    datei_abs = Path(WORKDIR) / datei_rel
    erwarteter_inhalt = (block + "\n").encode("utf-8")

    schon_da = False
    if datei_abs.exists():
        try:
            schon_da = datei_abs.read_bytes() == erwarteter_inhalt
        except OSError:
            schon_da = False

    if not schon_da:
        datei_abs.parent.mkdir(parents=True, exist_ok=True)
        datei_abs.write_bytes(erwarteter_inhalt)
        subprocess.run(["git", "add", datei_rel], cwd=WORKDIR, env=ENV, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"Akzeptanztests aus #{n}"],
                       cwd=WORKDIR, env=ENV, check=True)

    return True


def sync_issue_branch_with_main(n: int) -> bool:
    """Führt origin/main in den Branch von WORKDIR zusammen.

    Mit cwd=WORKDIR, env=ENV:
      1. ["git", "fetch", "-q", "origin"]                                   (check=True)
      2. ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"]        (check=False)
         Exit 0 -> schon aktuell: True, ohne weiteren Befehl.
      3. ["git", "merge", "--no-edit", "origin/main"]                        (check=False)
         Exit 0 -> True.
      4. sonst ["git", "merge", "--abort"] (check=False), Logzeile
         „Warnung: issue<N>: origin/main lässt sich nicht in den Branch mergen (Konflikt).“ und False.
    Kein Fehler dieser Funktion bricht den Lauf ab: bei False arbeitet der Agent auf dem unveränderten
    Branch weiter. Ein Zweig auf einem alten main lief sonst in den Pre-Push-Hook, der rote Tests
    meldet, obwohl der Fix längst auf main liegt (#208)."""
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=WORKDIR, env=ENV, check=True)
    aktuell = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        cwd=WORKDIR,
        env=ENV,
        check=False,
    )
    if aktuell.returncode == 0:
        return True

    gemergt = subprocess.run(["git", "merge", "--no-edit", "origin/main"],
                             cwd=WORKDIR, env=ENV, check=False)
    if gemergt.returncode == 0:
        return True

    # Nichts automatisch auflösen: der Agent entscheidet im Lauf, der Branch bleibt unverändert.
    subprocess.run(["git", "merge", "--abort"], cwd=WORKDIR, env=ENV, check=False)
    log(f"Warnung: issue{n}: origin/main lässt sich nicht in den Branch mergen (Konflikt).")
    return False


def open_pr(n: int) -> bool:
    """Legt nach dem Agentenlauf den PR zu Issue n an. True, wenn es danach einen offenen PR von
    issue_branch(n) gibt, sonst False.

    Wurde der PR in diesem Lauf neu angelegt, steht seine Nummer in `LAST_OPENED_PR` (für das
    `pr_opened`-Ereignis in main()); sonst ist das Modulglobal None.

    1. gh("pr", "list", "--state", "open", "--head", branch, "--json", "number"): nicht leer -> True,
       sonst nichts weiter (kein Push, kein Anlegen); LAST_OPENED_PR bleibt None.
    2. ["git", "rev-list", "--count", "origin/main..HEAD"] (cwd=WORKDIR, env=ENV, check=True): Zahl <= 1
       (nur der Akzeptanztest-Commit oder weniger) -> False, nichts weiter; Logzeile
       f"issue{n}: keine Änderungen über den Akzeptanztest hinaus, kein PR."
    3. ["git", "push", "-u", "origin", branch] (cwd=WORKDIR, env=ENV, check=False): Exit != 0 -> False,
       keine weitere Aktion, Zeile alarm(f"issue{n}", "Push abgelehnt (Tests rot oder GitHub-Fehler)").
       Der Ausgabetext des Befehls kommt nie ins Log (Regel 5).
    4. ["git", "log", "--reverse", "--format=%B%x00", "origin/main..HEAD"] (cwd=WORKDIR, env=ENV,
       check=True): stdout an NUL trennen, die erste Nachricht (Akzeptanztest-Commit) weglassen, leere
       verwerfen, die übrigen getrimmt mit je einer Leerzeile dazwischen zu `summary` verbinden.
    5. title = gh("issue", "view", str(n), "--json", "title")["title"];
       body = build_pr_body(pr_template(), n, WORKER, summary);
       gh("pr", "create", "--title", title, "--body", body, "--head", branch). gh gibt die PR-URL
       aus; daraus merkt sich LAST_OPENED_PR die Nummer. Rückgabe True."""
    global LAST_OPENED_PR
    LAST_OPENED_PR = None
    branch = issue_branch(n)
    # Bewusst ohne --limit: tests/acceptance/test_issue_172.py prüft diesen gh-Aufruf zeichengenau
    # (gemergt, unveränderlich). Einmal je fertigem Issue ist die Abfrage auch kein Kontingentfresser.
    offen = gh("pr", "list", "--state", "open", "--head", branch, "--json", "number")
    if offen:
        return True

    if not os.path.isdir(WORKDIR):
        log(f"issue{n}: Arbeitsverzeichnis fehlt, kein PR.")
        return False

    res_count = subprocess.run(
        ["git", "rev-list", "--count", "origin/main..HEAD"],
        cwd=WORKDIR,
        env=ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    count_str = (res_count.stdout or "").strip()
    try:
        count = int(count_str)
    except ValueError:
        count = 0
    if count <= 1:
        log(f"issue{n}: keine Änderungen über den Akzeptanztest hinaus, kein PR.")
        return False

    res_push = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        cwd=WORKDIR,
        env=ENV,
        check=False,
        capture_output=True,
        text=True,
    )
    if res_push.returncode != 0:
        alarm(f"issue{n}", "Push abgelehnt (Tests rot oder GitHub-Fehler)")
        return False

    res_log = subprocess.run(
        ["git", "log", "--reverse", "--format=%B%x00", "origin/main..HEAD"],
        cwd=WORKDIR,
        env=ENV,
        check=True,
        capture_output=True,
        text=True,
    )
    raw_msgs = (res_log.stdout or "").split("\x00")
    msgs = [m.strip() for m in raw_msgs[1:] if m.strip()]
    summary = "\n\n".join(msgs)

    title = gh("issue", "view", str(n), "--json", "title")["title"]
    body = build_pr_body(pr_template(), n, WORKER, summary)
    # gh pr create gibt die URL des neuen PRs aus (…/pull/<nummer>); daraus die Nummer für
    # das pr_opened-Ereignis. Ein zusätzliches `gh pr list` würde den zeichengenauen Aufruf
    # in tests/acceptance/test_issue_172.py verletzen.
    antwort = gh("pr", "create", "--title", title, "--body", body, "--head", branch)
    treffer = re.search(r"/pull/(\d+)", str(antwort or ""))
    LAST_OPENED_PR = int(treffer.group(1)) if treffer else None
    return True


_DEP_LINE_RE = re.compile(r"^\s*Wartet\s+auf\s*:\s*(.+)$", re.IGNORECASE)
_DEP_ISSUE_RE = re.compile(r"#(\d+)")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def parse_dependencies(body: str | None) -> list[int]:
    """Issue-Nummern aus Zeilen der Form „Wartet auf: #X, #Y“; einmal je Nummer, in Textreihenfolge.

    Zeilen innerhalb von Code-Blöcken (``` oder ~~~) sind Beispieltext und zählen nicht: Der
    Akzeptanztest eines Issues kann selbst „Wartet auf:“ enthalten, ohne eine Abhängigkeit zu sein.
    """
    if not body:
        return []
    numbers: list[int] = []
    in_fence = False
    for line in body.splitlines():
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _DEP_LINE_RE.match(line)
        if not m:
            continue
        for num in _DEP_ISSUE_RE.findall(m.group(1)):
            n = int(num)
            if n not in numbers:
                numbers.append(n)
    return numbers


def count_unblock_dependencies(blocked_issues: Iterable[dict]) -> dict[int, int]:
    """Zählt je Ziel-Issue, wie viele blockierte Issues darauf warten (`Wartet auf: #N`)."""
    counts: dict[int, int] = {}
    for issue in blocked_issues or []:
        for target in parse_dependencies(issue.get("body")):
            counts[target] = counts.get(target, 0) + 1
    return counts


def blocked_dependency_counts() -> dict[int, int]:
    """Unblock-Hebel: Ziel-Issue -> Anzahl der offenen `blockiert`-Issues, die darauf warten.

    Fragt höchstens `BLOCKED_ISSUES_LIMIT` Issues ab. Wirft nie: Scheitert `gh` oder kommt keine
    Liste zurück, ist der Hebel unbekannt und es gilt 0 für jedes Issue — die Auswahl läuft mit
    den übrigen Kriterien weiter, statt den Takt anzuhalten (#240).
    """
    try:
        blocked = gh("issue", "list", "--state", "open", "--label", "blockiert",
                     "--limit", str(BLOCKED_ISSUES_LIMIT), "--json", "number,body")
    except Exception as e:  # noqa: BLE001 — gh-Fehler dürfen die Auswahl nicht anhalten
        log(f"Warnung: blockierte Issues nicht abfragbar, Unblock-Hebel entfällt "
            f"({type(e).__name__}).")
        return {}
    if not isinstance(blocked, list):
        return {}
    return count_unblock_dependencies(blocked)


def _issue_prio_score(issue: dict) -> float:
    """Höchstes Gewicht der Prioritäts-Labels; ohne passendes Label `DEFAULT_PRIO_SCORE`."""
    best: float | None = None
    for label in issue.get("labels") or []:
        name = label.get("name", "") if isinstance(label, dict) else str(label)
        weight = PRIO_WEIGHTS.get(name)
        if weight is not None and (best is None or weight > best):
            best = weight
    return DEFAULT_PRIO_SCORE if best is None else best


def calculate_issue_score(
    issue: dict,
    dependency_counts: Mapping[int, int] | None = None,
    now: float | None = None,
) -> float:
    """Gesamtscore eines Issue-Kandidaten: Priorität + Unblock-Hebel + Anti-Starvation.

    `createdAt` ist UTC-ISO-8601 aus `gh issue list`. Fehlt es oder ist es unlesbar, bleibt der
    Altersbonus 0,0 statt zu scheitern; ein Zeitpunkt in der Zukunft zählt ebenfalls als 0,0.
    """
    if now is None:
        now = jetzt_s()
    score = _issue_prio_score(issue)
    score += (dependency_counts or {}).get(issue.get("number"), 0) * UNBLOCK_WEIGHT
    created_raw = issue.get("createdAt")
    if isinstance(created_raw, str) and created_raw:
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            age_days = (now - created.timestamp()) / 86400.0
        except (ValueError, TypeError, OverflowError):
            age_days = 0.0
        score += max(0.0, age_days) * AGING_WEIGHT_PER_DAY
    return float(score)


def issue_job(skip: Callable[[str], bool] | None = None, now: float | None = None,
              state: dict | None = None):
    """(Schlüssel, Auftrag, None, None) für das älteste freie Issue ohne Konflikt oder None.

    Neu: die Auswahl ändert KEINE Labels mehr — kein claim_issue-Aufruf. Der Auftragstext bleibt
    wörtlich wie bisher; Schritt 1 des Auftrags an das Modell bleibt
    `gh issue edit <N> --add-label in-arbeit --remove-label bereit`.

    Neu (#32):
    - `active_in_progress_issues() is None` -> kein Auftrag (Rückgabe None), Logzeile mit Grund.
    - Hat ein aktiver Auftrag keine belegte Dateiliste (`parse_affected_files() is None`),
      wird kein Auftrag gebaut (Rückgabe None): die Lage ist nicht beurteilbar.
    - Hat ein Kandidat keine belegte Dateiliste, wird genau dieser Kandidat übersprungen
      (Logzeile mit Issue-Nummer) und der nächste geprüft.
    Sonst unverändert: ältestes Issue zuerst, Fremd-Issues nie, Kollisionen wie bisher.

    `skip`: geparkte Kandidaten werden übersprungen — ohne Claim, und der nächste kommt dran.

    Der Auftrag nennt jeden Schritt mit Befehl und enthält das Issue wörtlich. Mit „Bearbeite die
    nächste Aufgabe nach AGENTS.md“ übersprang Qwen Label, Branch und Akzeptanz-Commit, arbeitete
    auf main und hörte beim ersten roten Test auf (#102, erster Lauf).
    """
    if now is None:
        now = jetzt_s()
    if state is None:
        state = load_state()
    SKIPPED.clear()
    issues = gh("issue", "list", "--state", "open", "--label", "worker-task", "--label", "bereit",
                "--label", LABEL, "--limit", str(ISSUE_LIST_LIMIT),
                "--search", "sort:created-asc -label:blockiert",
                "--json", "number,title,body,author,labels")
    # Wie bei Reviews: Nur Issues von Stephans Konto werden zum Auftrag mit Schreibrecht.
    issues = [i for i in issues or [] if (i.get("author") or {}).get("login") == OWNER]

    # #240: Prioritäts-Labels, Unblock-Hebel und Alter vor der Regel-8-Prüfung verrechnen. `sort`
    # ist stabil, damit gleiche Scores die bisherige Reihenfolge (created-asc) behalten.
    dep_counts = blocked_dependency_counts()
    issues.sort(key=lambda cand: calculate_issue_score(cand, dep_counts, now=now), reverse=True)

    # Konfliktprüfung mit aktiven Aufträgen (Regel 8: main.py und dieselben Dateien).
    # Unbekannt heißt blockieren: Ohne belegte Lage baut der Takt keinen Auftrag (#32).
    active = active_in_progress_issues()
    if active is None:
        # Lage unbekannt (gh-Fehler): _kollision:-Einträge bleiben absichtlich erhalten.
        log("Kein Auftrag: aktive Aufträge nicht abfragbar (Regel 8 nicht beurteilbar).")
        return None

    # #207: Auch ohne `bereit`-Kandidaten freigeben. Standen alle Issues auf `blockiert`,
    # kehrte issue_job vorher zurück, und der verwaiste Claim blieb genau in dem Fall liegen,
    # für den cleanup_orphan_claims gedacht ist.
    if active:
        active = cleanup_orphan_claims(active, now=now)

    if not issues:
        kollision_geaendert = False
        for k in list(state.keys()):
            if k.startswith(KOLLISION):
                del state[k]
                kollision_geaendert = True
        if kollision_geaendert:
            save_state(state)
        return None

    active_touches_main = any(touches_main_py(a) for a in active)
    active_files = set()
    for a in active:
        a_files = parse_affected_files(a.get("body", ""))
        if a_files is None:
            # Dateiliste unbekannt: keine Lagebeurteilung möglich, _kollision:-Einträge bleiben erhalten.
            log(f"Kein Auftrag: aktiver Auftrag #{a.get('number')} hat keine belegte "
                "Dateiliste (Regel 8 nicht beurteilbar).")
            return None
        active_files |= a_files
        if touches_main_py(a):
            active_files.add(HOTSPOT)

    current_collisions: set[tuple[int, int]] = set()
    kollision_geaendert = False
    selected = None
    for cand in issues:
        cand_no = cand["number"]
        cand_body = cand.get("body", "")
        if skip and skip(f"issue{cand_no}"):
            continue   # geparkt: der nächste Kandidat kommt dran, kein claim_issue
        cand_touches_main = touches_main_py(cand)

        # Regel 8: Ohne belegte Dateiliste ist der Kandidat nicht beurteilbar -> überspringen.
        cand_files = parse_affected_files(cand_body)
        if cand_files is None:
            log(f"Issue #{cand_no} übersprungen: keine belegte Dateiliste (Regel 8).")
            SKIPPED.append(f"#{cand_no}: keine belegte Dateiliste")
            continue

        # Regel 8: Keine zwei Agenten an main.py
        if cand_touches_main and active_touches_main:
            log(f"Issue #{cand_no} übersprungen: berührt {HOTSPOT}, während bereits ein Auftrag "
                f"an {HOTSPOT} in Arbeit ist (Regel 8).")
            SKIPPED.append(f"#{cand_no}: berührt {HOTSPOT}, während bereits ein Auftrag "
                           f"an {HOTSPOT} in Arbeit ist")
            for a in active:
                if touches_main_py(a):
                    if _track_kollision(cand_no, a.get("number"), now, current_collisions, state):
                        kollision_geaendert = True
            continue

        # Regel 8: Keine zwei Agenten in denselben Dateien; Doku-Pflichtzeilen und der
        # Akzeptanztest-Platzhalter zählen dabei nicht (#189).
        if cand_touches_main:
            cand_files.add(HOTSPOT)
        overlap = collision_files(cand_files) & collision_files(active_files)
        if overlap:
            log(f"Issue #{cand_no} übersprungen: Datei-Kollision mit aktivem Auftrag auf "
                f"{', '.join(sorted(overlap))} (Regel 8).")
            SKIPPED.append(f"#{cand_no}: Datei-Kollision mit aktivem Auftrag auf "
                           f"{', '.join(sorted(overlap))}")
            for a in active:
                a_files = parse_affected_files(a.get("body", "")) or set()
                if touches_main_py(a):
                    a_files.add(HOTSPOT)
                if collision_files(cand_files) & collision_files(a_files):
                    if _track_kollision(cand_no, a.get("number"), now, current_collisions, state):
                        kollision_geaendert = True
            continue

        selected = cand
        break

    active_kollision_keys = {f"{KOLLISION}{c}:{b}" for (c, b) in current_collisions}
    for k in list(state.keys()):
        if k.startswith(KOLLISION) and k not in active_kollision_keys:
            del state[k]
            kollision_geaendert = True

    if kollision_geaendert:
        save_state(state)

    if not selected:
        return None

    n, branch = selected["number"], issue_branch(selected["number"])

    task = f"""Du setzt Issue #{n} um (AGENTS.md, Worker-Aufgaben → Worker). Du bist Worker: {WORKER}.
Das Issue steht unten; es ist der Vertrag. Gehe genau diese Schritte durch und führe jeden als Befehl aus:

1. `gh issue edit {n} --add-label in-arbeit --remove-label bereit`
2. Der Takt hat den Branch {branch} ausgecheckt und den Akzeptanztest bereits als Commit gesichert.
3. Die Datei tests/acceptance/test_issue_{n}.py darf der Agent nie ändern.
4. Setze das Issue um, nur in den Dateien unter „### Betroffene Dateien“. Steht dort, dass sich
   die Erwartung eines bestehenden Tests ändert, passe genau diese Stelle an. Ergänze die Tests
   aus „### Fertig, wenn“.
5. `{TEST_CMD}`. Ist ein Test rot: Fehlermeldung lesen, Code (nicht den Akzeptanztest)
   korrigieren, Schritt 5 wiederholen. Ein roter Test ist kein Grund aufzuhören, sondern der
   nächste Arbeitsschritt. Erst weiter, wenn alles grün ist.
6. Alle Änderungen in Commits sichern (`git add -A && git commit -m "<was und warum>"`). Die
   Nachricht jedes Commits beschreibt, was und warum geändert wurde (sie wird zum PR-Text), und
   aufhören. Push und Pull Request legt der Takt an; du pushst nicht und legst keinen PR an.
   {PUSH_RULE}
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
    """Nicht still scheitern: Gibt es nach dem Lauf einen PR vom Branch des Issues?

    Ein unvollständiger PR-Text wird sofort ohne Modelllauf vervollständigt (#234); erst wenn das
    scheitert, landet die Warnung im Log und der nächste Takt trägt den Text nach."""
    n = int(key.removeprefix("issue"))
    prs = gh("pr", "list", "--state", "open", "--head", issue_branch(n), "--json", "number,body")
    if prs:
        log(f"{key}: PR #{prs[0]['number']} von {issue_branch(n)} ist offen.")
        autofix_pr_text(prs[0]["number"], n, prs[0].get("body"))
    else:
        log(f"WARNUNG {key}: kein PR von {issue_branch(n)}, Issue nicht erledigt.")


def autofix_pr_text(pr_no: int, issue_no: int, body: str | None = None) -> bool:
    """Vervollständigt einen PR-Text ohne Modelllauf (`gh pr edit`), True bei Erfolg.

    Fehlt nichts (`pr_text_problems`), gibt die Funktion False zurück und ruft kein `gh` auf.
    Sonst baut sie mit `worker_frame.build_pr_body` einen vollständigen Text aus `pr_template()` und
    `WORKER`, schreibt ihn per `gh("pr", "edit", …)` und loggt die fehlenden Teile. Scheitert `gh`,
    fällt sie auf `warn_text` zurück (nächster Takt trägt nach) und gibt False zurück."""
    missing = pr_text_problems(body, issue_no)
    if not missing:
        return False
    text = worker_frame.build_pr_body(pr_template(), issue_no, WORKER)
    try:
        gh("pr", "edit", str(pr_no), "--body", text)
    except Exception:
        warn_text(f"issue{issue_no}", pr_no, body, issue_no)
        return False
    log(f"PR #{pr_no}: PR-Text deterministisch vervollständigt ({', '.join(missing)}).")
    return True


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


def format_usage(usage: object) -> str:
    """Token-Angabe für die Schlusszeile, z. B. ', Token: 1200 ein/340 aus/9800 Cache'.

    Liest input_tokens, output_tokens und cache_read_input_tokens (je optional, nur ganze
    Zahlen >= 0 zählen). Gibt '' zurück, wenn usage kein dict ist oder keiner der drei Werte
    brauchbar vorkommt. Fehlende Einzelwerte erscheinen als 0. Wirft nie eine Ausnahme.
    """
    try:
        if not isinstance(usage, dict):
            return ""
        # bool ausschließen: True/False sind in Python ganze Zahlen und wären sonst „1“/„0“.
        zahlen = [v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
                  for v in (usage.get(k) for k in
                            ("input_tokens", "output_tokens", "cache_read_input_tokens"))]
    except Exception:
        # Vertrag: „wirft nie eine Ausnahme“ — die Zeile entsteht im Logpfad des Laufs und darf
        # ihn nicht abbrechen, auch nicht bei einem usage mit eigenwilligem get().
        return ""
    if all(v is None for v in zahlen):
        return ""
    ein, aus, cache = (0 if v is None else v for v in zahlen)
    return f", Token: {ein} ein/{aus} aus/{cache} Cache"


# Kurztext des Anbieterfehlers im letzten Agentenlauf, sonst None. Nur log_stream_line setzt ihn;
# main() liest ihn nach run_agent() und nimmt dann den Versuch zurück (#106).
LAST_AGENT_ERROR: str | None = None
# Token-Usage und Cost des letzten Agentenlaufs für audit("agent_run_finished", ...) (#222).
# Wird von log_stream_line() (Qwen) befüllt; für OpenCode bleibt None.
LAST_AGENT_USAGE: dict | None = None
# Nummer des PRs, den open_pr() in diesem Lauf neu angelegt hat, sonst None (#222).
LAST_OPENED_PR: int | None = None

_API_ERROR_PREFIX = "[API Error:"
# Bei LiteLLM folgt auf ein Leerzeichen „Key“ (bzw. „Received“) der Schlüsselteil samt Key-Hash;
# alles ab dort gehört nicht ins Log (Regel 5).
_API_ERROR_CUT_RE = re.compile(r"\s+(?:Key|Received)\b")
_API_ERROR_KEY_RE = re.compile(r"\bsk-\S*")
_API_ERROR_CODE_RE = re.compile(r"^(\d{3})\b")
_API_ERROR_MAX = 160


def short_api_error(message: str) -> str:
    """Einzeilige, schlüsselfreie Kurzfassung einer Fehlermeldung aus `error.message` (#106).

    - Umschließendes „[API Error: “ und „]“ entfallen.
    - Beginnt der Rest mit einem dreistelligen Statuscode: „API <Code>: <Text>“, sonst „API: <Text>“.
    - <Text> endet vor dem ersten „ Key“/„ Received“ (dort folgen bei LiteLLM Schlüsselteil und -Hash).
    - Danach `redact()` und jedes Wort, das mit „sk-“ beginnt, entfällt (Regel 5).
    - Zeilenumbrüche und Mehrfach-Leerzeichen werden ein Leerzeichen; höchstens 160 Zeichen,
      Gekürztes endet auf „…“. Reine Funktion, kein Netz."""
    text = message.strip()
    if text.startswith(_API_ERROR_PREFIX):
        text = text[len(_API_ERROR_PREFIX):].strip()
    if text.endswith("]"):
        text = text[:-1].strip()
    cut = _API_ERROR_CUT_RE.search(text)
    if cut:
        text = text[:cut.start()].strip()
    text = _API_ERROR_KEY_RE.sub("", redact(text))
    text = " ".join(text.split())
    m = _API_ERROR_CODE_RE.match(text)
    result = f"API {m.group(1)}: {text[m.end():].strip()}" if m else f"API: {text}"
    if len(result) > _API_ERROR_MAX:
        result = result[: _API_ERROR_MAX - 1].rstrip() + "…"
    return result


def log_stream_line(line: str) -> None:
    """Eine Zeile stream-json von Qwen Code als lesbares Log: Text, `$ befehl`, Ausgabe.

    Ein Ergebnis-Ereignis mit Anbieterfehler ({"type": "result", "is_error": true,
    "subtype": "error_during_execution", "error": {"message": "[API Error: …"}}) setzt zusätzlich
    LAST_AGENT_ERROR und schreibt dessen Kurzfassung als eigene Logzeile (#106). Jede Zeile
    landet zusätzlich im Merker für `detect_infra_error` (#243)."""
    global LAST_AGENT_ERROR
    merke_agent_zeile(line)

    def say(text: str) -> None:
        # Regel 5: keine Geheimniswerte aus der Werkzeugausgabe ins Log (#35).
        print(redact(text), flush=True)

    try:
        event = json.loads(line)
    except ValueError:
        if line.strip():
            say(line.rstrip())
        return
    kind = event.get("type")
    content = (event.get("message") or {}).get("content") or []
    if kind == "assistant":
        for part in content:
            if part.get("type") == "text" and part.get("text", "").strip():
                say(part["text"].strip())
            elif part.get("type") == "tool_use":
                args = part.get("input") or {}
                if part.get("name") == "run_shell_command":
                    say(f"$ {args.get('command', '')}")
                else:
                    target = args.get("file_path") or args.get("path") or args.get("pattern") or ""
                    say(f"→ {part.get('name')} {target}".rstrip())
    elif kind == "user":
        for part in content:
            if part.get("type") == "tool_result":
                out = part.get("content")
                out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                mark = "✗ Fehler:\n" if part.get("is_error") else ""
                say(mark + _clip(out))
    elif kind == "result":
        err = event.get("error")
        message = err.get("message") if isinstance(err, dict) else None
        if (event.get("is_error") and event.get("subtype") == "error_during_execution"
                and isinstance(message, str) and message.startswith(_API_ERROR_PREFIX)):
            LAST_AGENT_ERROR = short_api_error(message)
            say(f"[Anbieterfehler] {LAST_AGENT_ERROR}")
        # Usage für agent_run_finished Audit-Event speichern (#222)
        usage = event.get("usage")
        if isinstance(usage, dict):
            global LAST_AGENT_USAGE
            LAST_AGENT_USAGE = usage
        say(f"[Qwen Code: {event.get('subtype')}, {event.get('num_turns')} Züge, "
            f"{round((event.get('duration_ms') or 0) / 1000)} s{format_usage(event.get('usage'))}]")


# #243: Infrastruktur- und Laufzeitfehler der Sandbox (fehlende Rechte, volles Laufwerk,
# unvollständige Node-Installation). Sie sagen nichts über den Auftrag aus: ein solcher Lauf darf
# keinen Versuch verbrauchen, und sein Claim darf nicht 45 Minuten als Phantom-Sperre stehen.
INFRA_ERROR_PATTERNS = (
    "EACCES",
    "permission denied",
    "ENOSPC",
    "MODULE_NOT_FOUND",
    "Cannot find module",
)

# Erster Infrastrukturfehler des laufenden Agenten, sonst None (#243). `merke_agent_zeile` setzt
# ihn während des Laufs; `main()` liest ihn nach `run_agent()` und nimmt dann den Versuch zurück.
# Damit bleibt der Treffer auch erhalten, wenn danach noch viel Ausgabe folgt.
AGENT_INFRA_FEHLER: str | None = None


def merke_agent_zeile(line: str) -> None:
    """Merkt einen Infrastrukturfehler aus einer Ausgabezeile des Agenten (#243)."""
    global AGENT_INFRA_FEHLER
    if AGENT_INFRA_FEHLER is None:
        AGENT_INFRA_FEHLER = detect_infra_error(line)


def detect_infra_error(output: str) -> str | None:
    """Kurzbeschreibung des ersten Infrastrukturfehlers in `output`, sonst None (#243).

    Gesucht wird zeilenweise nach `INFRA_ERROR_PATTERNS`; zurück kommt die getrimmte
    Trefferzeile mit höchstens 120 Zeichen, damit Logzeile und Issue-Kommentar einzeilig
    bleiben. Echte Testfehlschläge (`AssertionError`, `FAILED …`) enthalten keins der Muster
    und zählen weiter als regulärer Versuch."""
    for zeile in output.splitlines():
        if any(muster in zeile for muster in INFRA_ERROR_PATTERNS):
            return zeile.strip()[:120]
    return None


def _rechte_ok(pfad: Path, uid: int, gids: list[int], schreiben: bool) -> bool:
    """Darf `uid` (mit Zusatzgruppen `gids`) `pfad` durchqueren und ggf. beschreiben?

    Prüft die Modusbits wie der Kernel: erst Eigentümer-, dann Gruppen-, sonst Sonstige-Bits.
    `root` umgeht die Bits (CAP_DAC_OVERRIDE) und darf immer."""
    try:
        st = pfad.stat()
    except OSError:
        return False
    if uid == 0:
        return True
    if st.st_uid == uid:
        bits = (st.st_mode >> 6) & 0o7
    elif st.st_gid in gids:
        bits = (st.st_mode >> 3) & 0o7
    else:
        bits = st.st_mode & 0o7
    if not bits & 0o1:
        return False
    return not schreiben or bool(bits & 0o2)


def sandbox_health_check(workdir: str | Path, user: str | None = None) -> bool:
    """Kann `user` (sonst dieser Prozess) in `workdir` arbeiten? (#243)

    Geprüft werden alle Elternverzeichnisse bis `/` (ein fehlendes `x` dort ergibt beim `chdir`
    des Agenten genau das EACCES aus #217) und der Arbeitsordner selbst (Schreibrecht für Klon,
    Branch und Push). `user` wird über `pwd` samt Zusatzgruppen aufgelöst; fehlt er, ist das ein
    Einrichtungsfehler. Jeder Fehlschlag loggt eine „⚠ Stephan:“-Zeile (Regel 7) und liefert
    False, damit `main()` ohne Claim abbricht."""
    pfad = Path(workdir)
    if not pfad.exists():
        log(f"⚠ Stephan: Sandbox-Check: Arbeitsordner {pfad} fehlt.")
        return False
    if not pfad.is_dir():
        log(f"⚠ Stephan: Sandbox-Check: {pfad} ist kein Verzeichnis.")
        return False
    try:
        if pfad.resolve() == Path(__file__).resolve().parents[1]:
            return True
    except OSError:
        pass
    if user is None:
        if os.access(pfad, os.X_OK | os.W_OK):
            return True
        log(f"⚠ Stephan: Sandbox-Check: {pfad} ist für diesen Prozess nicht betretbar "
            "oder nicht schreibbar.")
        return False
    if pwd is None:
        log("⚠ Stephan: Sandbox-Check: Nutzerprüfung ohne pwd nicht möglich (Windows).")
        return False
    try:
        eintrag = pwd.getpwnam(user)
    except KeyError:
        log(f"⚠ Stephan: Sandbox-Check: Nutzer {user} fehlt; sandbox/install.sh legt ihn an.")
        return False
    uid = eintrag.pw_uid
    try:
        gids = os.getgrouplist(user, eintrag.pw_gid)
    except (AttributeError, OSError):
        gids = [eintrag.pw_gid]
    for ordner in (pfad, *pfad.parents):
        if not _rechte_ok(ordner, uid, gids, schreiben=ordner == pfad):
            art = "schreibbar" if ordner == pfad else "durchquerbar"
            log(f"⚠ Stephan: Sandbox-Check: {ordner} ist für Nutzer {user} nicht {art}.")
            return False
    return True


def release_failed_claim(n: int, grund: str) -> bool:
    """Gibt den Claim eines infrastrukturell gescheiterten Laufs sofort zurück (#243).

    Ohne diesen Schritt bliebe das Issue 45 Minuten auf `in-arbeit` und sperrte über Regel 8
    jeden weiteren Auftrag an denselben Dateien. True, wenn `gh` den Aufruf annimmt."""
    log(f"issue{n}: Infrastrukturfehler ({grund}), gebe Claim sofort frei (in-arbeit -> bereit).")
    try:
        gh("issue", "edit", str(n), "--add-label", "bereit", "--remove-label", "in-arbeit")
        return True
    except Exception as e:
        log(f"issue{n}: Freigabe des Claims fehlgeschlagen ({type(e).__name__}).")
        return False


# #217: Fehlt der Agenten-Nutzer, läuft der Agent als root – das ist der ungeschützte Zustand,
# den #217 beheben soll. Deshalb genau eine „⚠ Stephan:“-Zeile je Takt-Prozess (Push-
# Benachrichtigung), die Wiederholung nur noch als WARNUNG, damit keine Flut entsteht.
_IDENTITAET_ALARMIERT = False


def agent_identitaet() -> tuple[str | None, str | None]:
    """(Nutzer, Home) für den Rechte-Drop des Agentenprozesses, sonst (None, None) (#217).

    Nur wenn der Takt selbst als root läuft und `AGENT_USER` existiert, startet der Agent
    unter diesem Nutzer; sonst erbt er Nutzer und Home des Takts wie bisher. Ein fehlender
    Nutzer ist ein Einrichtungsfehler (sandbox/install.sh legt ihn an) und läuft deshalb
    nicht still als root weiter: die erste Meldung ist eine „⚠ Stephan:“-Zeile (Regel 7)."""
    global _IDENTITAET_ALARMIERT
    if os.name == "nt" or os.geteuid() != 0:
        return None, None
    try:
        eintrag = pwd.getpwnam(AGENT_USER)
    except KeyError:
        text = (f"Nutzer {AGENT_USER} fehlt; der Agent läuft als root. "
                "sandbox/install.sh legt ihn an.")
        if _IDENTITAET_ALARMIERT:
            log(f"WARNUNG: {text}")
        else:
            _IDENTITAET_ALARMIERT = True
            log(f"⚠ Stephan: Agent: Einrichtung: {text}")
        return None, None
    return AGENT_USER, eintrag.pw_dir


def run_agent(prompt: str) -> int:
    """Startet den Agenten; `timeout` beendet ihn hart, falls er selbst nicht aufhört.

    Der Agentenprozess bekommt `agent_env(ENV)` statt `ENV`: nur die Allowlist und der
    Modellschlüssel, sonst keine Geheimnisse (#35). `ENV` statt `os.environ` ist nötig, weil nur dort der PATH-Zusatz
    `/root/venv/bin:/root/.opencode/bin` steht; die Worker-Units setzen keinen PATH, und
    `pytest` liegt nur im venv (#72). Läuft der Takt als root, startet der Agent als
    `AGENT_USER` mit dessen Home (#217); damit reicht ein Ausbruch aus dem Werkzeugkasten
    nicht mehr an die Geheimnisse des Hosts. Die bei OpenCode ungefiltert gedruckte Ausgabe
    läuft durch `redact()`, damit kein Schlüssel in `/root/worker.log` und damit zu Home
    Assistant kommt. Mit `user` geht auch `group` mit (#243): ohne eigenen GID behielte das
    Kind die Gruppe des Takts (auf dem Server 0) und der Rechte-Drop wäre nur halb.

    Gibt den Exit-Code zurück. Token-Usage und Cost des Laufs stehen danach in
    `LAST_AGENT_USAGE` (dict oder None, #222); die Rückgabe bleibt bewusst ein int, weil
    tests/acceptance/test_issue_106.py und -178.py sie zeichengenau prüfen."""
    global LAST_AGENT_USAGE
    LAST_AGENT_USAGE = None
    cmd = ["timeout", "-k", "30", str(RUN_TIMEOUT + 60), *agent_cmd(prompt)]
    env = agent_env(ENV)
    user, home = agent_identitaet()
    if user:
        # gh und qwen suchen ihre Konfiguration im Home des laufenden Nutzers, nicht in /root.
        env["HOME"] = home
    with subprocess.Popen(cmd, cwd=WORKDIR, env=env, user=user, group=user,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                          errors="replace") as proc:
        for line in proc.stdout:
            if AGENT == "opencode":
                merke_agent_zeile(line)   # geht nicht durch log_stream_line (#243)
                print(redact(line), end="", flush=True)
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
    LiteLLM erst beim Auftrag; ein leeres Tagesbudget erkennt der Takt davor (budget_problem).
    """
    try:
        with urllib.request.urlopen(f"{LITELLM}/health/liveliness", timeout=10):
            return None
    except Exception:
        return "LiteLLM nicht erreichbar"


def jetzt_s() -> float:
    """Sekunden seit der Epoche; eigene Funktion, damit Tests die Uhr setzen können (#180)."""
    return time.time()


def litellm_down(state: dict, now: float | None = None) -> bool:
    """Einen andauernden LiteLLM-Ausfall als „⚠ Stephan:“-Zeile melden (#180).

    Merkt den ersten Fehlschlag in `state[LITELLM_AB]` und behält ihn bis `litellm_up()`;
    erst ab `LITELLM_ALARM_S` Sekunden danach entsteht genau eine Zeile je Ausfall. Nur der
    erste Slot (ohne Nummer: der einzige Worker) meldet, sonst käme dieselbe Meldung doppelt.
    True nur beim Melden, alle anderen Aufrufe False."""
    if WORKER_SLOT not in ("", "1"):
        return False
    if now is None:
        now = jetzt_s()
    start = state.get(LITELLM_AB)
    if not isinstance(start, (int, float)) or isinstance(start, bool):
        state[LITELLM_AB] = now          # erster Fehlschlag dieses Ausfalls
        save_state(state)
        return False
    if state.get(LITELLM_ALARM):
        return False                     # dieser Ausfall wurde schon gemeldet
    if now - start < LITELLM_ALARM_S:
        return False
    minuten = int((now - start) // 60)
    alarm("-", f"LiteLLM seit {minuten} Minuten nicht erreichbar; Worker und Review stehen")
    state[LITELLM_ALARM] = True
    save_state(state)
    return True


def litellm_up(state: dict, now: float | None = None) -> bool:
    """Das Ende eines gemerkten LiteLLM-Ausfalls aufräumen (#180).

    Entfernt beide Ausfallschlüssel und speichert; True nur, wenn zuvor alarmiert wurde, dann
    steht die Entwarnung im Log (ohne „⚠“). Nach einem kurzen Aussetzer bleibt es still, und
    ohne gemerkten Ausfall wird nichts gespeichert."""
    if WORKER_SLOT not in ("", "1"):
        return False
    if LITELLM_AB not in state:
        return False
    if now is None:
        now = jetzt_s()
    start = state.pop(LITELLM_AB)
    alarmiert = bool(state.pop(LITELLM_ALARM, False))
    save_state(state)
    if not alarmiert:
        return False
    minuten = int((now - start) // 60) if isinstance(start, (int, float)) else 0
    log(f"LiteLLM wieder erreichbar nach {minuten} Minuten.")
    return True


def stillstand_pruefen(state: dict, gruende: list[str], now: float | None = None) -> bool:
    """Merkt und meldet einen Stillstand (#218). Speichert nicht (main speichert danach).

    - Nur der erste Slot (WORKER_SLOT "", "1" oder "w1") prüft; sonst False, state unberührt.
    - `gruende` leer: `state["_stillstand"]` entfernen, False.
    - Kein oder unbrauchbarer Eintrag: state["_stillstand"] = {"seit": now, "gemeldet": False}, False.
    - Dauer >= STILLSTAND_MINUTEN * 60 und noch nicht gemeldet: gemeldet = True und genau eine
      alarm("-", "Stillstand seit <M> Minuten: <bis zu 3 Gründe, mit „; “ getrennt, ggf. „; +K weitere“>"),
      Rückgabe True. Sonst False. `now` ist Standard jetzt_s().
    """
    if WORKER_SLOT not in ("", "1", "w1"):
        return False
    if now is None:
        now = jetzt_s()
    if not gruende:
        state.pop(STILLSTAND, None)
        return False
    eintrag = state.get(STILLSTAND)
    seit = eintrag.get("seit") if isinstance(eintrag, dict) else None
    if not isinstance(seit, (int, float)) or isinstance(seit, bool):
        state[STILLSTAND] = {"seit": now, "gemeldet": False}
        return False
    if eintrag.get("gemeldet"):
        return False
    if now - seit < STILLSTAND_MINUTEN * 60:
        return False
    eintrag["gemeldet"] = True
    minuten = int((now - seit) // 60)
    text = "; ".join(gruende[:3])
    weitere = len(gruende) - 3
    if weitere > 0:
        text += f"; +{weitere} weitere"
    alarm("-", f"Stillstand seit {minuten} Minuten: {text}")
    return True


def key_info() -> dict | None:
    """Verbrauch und Limit des eigenen Worker-Keys aus GET {LITELLM}/key/info.

    None bei jedem Fehler (HTTP, Netz, Timeout, kein JSON, Feld „info“ fehlt): Der Takt hält
    eine unklare Lage bewusst nicht an, ein defektes /key/info darf die Pipeline nicht
    stilllegen. Der Schlüssel steht nur im Header und kommt nie ins Log (Regel 5).
    """
    req = urllib.request.Request(
        f"{LITELLM}/key/info",
        headers={"Authorization": f"Bearer {os.environ.get('LITELLM_API_KEY', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception:
        return None
    info = data.get("info") if isinstance(data, dict) else None
    return info if isinstance(info, dict) else None


def budget_problem(info: dict | None) -> str | None:
    """Meldung, wenn das Tagesbudget des Worker-Keys aufgebraucht ist, sonst None.

    Ist es leer, antwortet LiteLLM jeder Anfrage mit 429 – auch den Ausweichmodellen; ein
    Lauf würde also nur einen Versuch verbrauchen (#90). „Spend >= max_budget“ ist der Beleg.
    Eine unklare Lage (keine Antwort, kein Limit, keine Zahlen) gibt None: Der Takt läuft
    weiter (anders als bei Regel 8). Die Meldung nennt keine Budgetwerte (Regel 5), nur den
    Reset-Zeitpunkt aus `budget_reset_at`.
    """
    if not isinstance(info, Mapping):
        return None
    spend, max_budget = info.get("spend"), info.get("max_budget")
    if not isinstance(spend, (int, float)) or not isinstance(max_budget, (int, float)):
        return None
    if spend < max_budget:
        return None
    reset = info.get("budget_reset_at")
    return f"LiteLLM-Tagesbudget aufgebraucht (Reset {str(reset)[:16] if reset else 'unbekannt'})"


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


def choose_model(primary: str | None = None) -> tuple[str | None, list[str]]:
    """Erstes Modell aus primary (oder LITELLM_MODEL) + LITELLM_FALLBACKS, das antwortet, und die Fehlschläge davor."""
    failed = []
    first = primary or LITELLM_MODEL
    for model in dict.fromkeys([first, *LITELLM_FALLBACKS]):
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


def job_parked(state: dict, key: str) -> bool:
    """True, wenn der Auftrag `key` im aktiven Projekt MAX_TRIES erreicht hat.

    Der Zustand wird nur um die Meldung ergänzt, nicht um einen Zähler: beim ersten Überspringen
    genau eine Logzeile, danach keine weitere (log_on_change merkt sich den Vorsatz-Schlüssel).

    Warum das in die Auswahl gehört und nicht in main(): Ein geparkter erster Kandidat ließ
    main() an derselben Stelle enden, sodass kein anderer Auftrag und kein anderes Projekt mehr
    dran kam (14.10.: `agent-sandbox-infra/issue14` stand auf 2 und legte die Warteschlange still).
    """
    skey = PREFIX + key
    if state.get(skey, 0) < MAX_TRIES:
        return False
    log_on_change(state, f"_park:{skey}", "geparkt",
                  f"{skey}: schon {MAX_TRIES} Versuche, wartet auf Stephan.")
    return True


def parked_job(state: dict) -> str | None:
    """Schlüssel des geparkten Auftrags, den job_parked zuletzt gemeldet hat, sonst None.

    job_parked legt beim Überspringen `_park:<skey>` im Zustand ab. main() nutzt diesen Hinweis,
    damit die Slot-Telemetrie im Stillstand den genauen Grund nennt („wartet auf Stephan“) statt
    nur „Warte auf nächsten Takt“ (#14). Die Auswahl selbst bleibt unberührt.
    """
    for key in state:
        if key.startswith("_park:"):
            return key[len("_park:"):]
    return None


def prune_parked(state: dict, still_open: Callable[[str], bool]) -> list[str]:
    """Entfernt zu jedem Schlüssel `_park:<skey>` im Zustand die Marke und den Zähler `state[skey]`,
    wenn `still_open(skey)` False liefert. Gibt die entfernten `skey` zurück (Reihenfolge wie im
    Zustand). Wirft `still_open`, bleibt die Marke (im Zweifel geparkt). Speichert nicht.
    """
    entfernt: list[str] = []
    for key in list(state):
        if not key.startswith("_park:"):
            continue
        skey = key[len("_park:"):]
        try:
            offen = still_open(skey)
        except Exception:
            continue    # unklare Lage: im Zweifel geparkt lassen (Regel 7)
        if offen:
            continue
        state.pop(key, None)
        state.pop(skey, None)
        state.pop(f"_versuch:{skey}", None)
        entfernt.append(skey)
    return entfernt


_PR_JOB_KEY_RE = re.compile(r"^(?:pr|text)(\d+)\s*@")
_ISSUE_JOB_KEY_RE = re.compile(r"^issue(\d+)$")


def _job_repo(skey: str) -> str | None:
    """Repository eines Zustandsschlüssels; None bei unbekanntem Projektvorsatz.

    Ohne Vorsatz gehört der Schlüssel dem ersten Projekt (so waren die Zustandsdateien vor der
    Projektliste). Mit `<projekt>/` zählt die Projektliste, nicht das gerade aktive Projekt."""
    if "/" not in skey:
        return PROJECTS[0].repo
    name = skey.split("/", 1)[0]
    for p in PROJECTS:
        if p.name == name:
            return p.repo
    return None


def job_still_open(skey: str) -> bool:
    """True, wenn der Auftrag noch offen ist. `skey` = PREFIX + Schlüssel:
    `pr<N> @<sha>` / `text<N> @<sha>` -> PR N offen (state OPEN); `issue<N>` -> Issue N offen.
    Projektvorsatz (`<projekt>/`) wählt das Repository. Unbekanntes Format oder gh-Fehler -> True.
    """
    repo = _job_repo(skey)
    if repo is None:
        return True
    key = skey.split("/", 1)[1] if "/" in skey else skey
    m = _PR_JOB_KEY_RE.match(key)
    if m:
        art, nummer = "pr", m.group(1)
    else:
        m = _ISSUE_JOB_KEY_RE.match(key)
        if not m:
            return True     # unbekanntes Format: nicht aufräumen
        art, nummer = "issue", m.group(1)
    try:
        antwort = gh(art, "view", nummer, "--json", "state", repo=repo)
    except Exception:
        return True         # gh-Fehler: im Zweifel geparkt lassen (Regel 7)
    if not isinstance(antwort, Mapping):
        return True
    zustand = str(antwort.get("state") or "").upper()
    if zustand == "OPEN":
        return True
    if zustand in ("CLOSED", "MERGED"):
        return False
    return True             # unlesbare Antwort: nichts entfernen


def idle_message(parked: str | None, skipped: list[str]) -> str:
    """Text der Leerlauf-Telemetrie. Ohne beides: "Warte auf nächsten Takt".
    Nur parked: "<parked>: wartet auf Stephan". Mit skipped: "Übersprungen (N): a; b; c"
    (höchstens 3 Einträge, bei mehr "; +K weitere"); ist zusätzlich parked gesetzt, hängt
    " | <parked>: wartet auf Stephan" an."""
    text = ""
    if skipped:
        gezeigt = "; ".join(skipped[:3])
        weitere = len(skipped) - 3
        text = f"Übersprungen ({len(skipped)}): {gezeigt}"
        if weitere > 0:
            text += f"; +{weitere} weitere"
    if parked:
        text = f"{text} | {parked}: wartet auf Stephan" if text else f"{parked}: wartet auf Stephan"
    return text or "Warte auf nächsten Takt"


def versuch_zaehlen(state: dict, skey: str, now: float | None = None) -> int:
    """Zählt einen Versuch: state[skey] += 1 und state[f"_versuch:{skey}"] = now (Standard jetzt_s()).
    Gibt den neuen Zählerstand zurück. main() ersetzt damit `state[skey] = state.get(skey, 0) + 1`."""
    aktuell = jetzt_s() if now is None else now
    neuer_stand = state.get(skey, 0) + 1
    state[skey] = neuer_stand
    state[f"_versuch:{skey}"] = aktuell
    return neuer_stand


def label_bereit_seit(skey: str) -> float | None:
    """Epoche-Sekunden des jüngsten Ereignisses „labeled“ mit dem Label `bereit` am Issue von `skey`.

    Nur Issue-Aufträge (`issue<N>`, mit oder ohne Projektvorsatz `<projekt>/`); sonst und bei unbekanntem
    Projektvorsatz None, ohne gh-Aufruf. Quelle: gh("api", f"repos/{repo}/issues/{n}/events?per_page=100", repo=repo)
    mit repo = _job_repo(skey). Kein passendes Ereignis: None. Ein gh-Fehler wird nicht gefangen."""
    repo = _job_repo(skey)
    if repo is None:
        return None
    key = skey.split("/", 1)[1] if "/" in skey else skey
    m = _ISSUE_JOB_KEY_RE.match(key)
    if not m:
        return None
    nummer = m.group(1)
    ereignisse = gh("api", f"repos/{repo}/issues/{nummer}/events?per_page=100", repo=repo)
    if not isinstance(ereignisse, list):
        return None
    juengst: float | None = None
    for e in ereignisse:
        if not isinstance(e, dict):
            continue
        if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == "bereit":
            ts_str = e.get("created_at")
            if ts_str:
                dt = _zeitpunkt(ts_str)
                if dt:
                    ts = dt.timestamp()
                    if juengst is None or ts > juengst:
                        juengst = ts
    return juengst


def entparken(state: dict, bereit_seit: Callable[[str], float | None]) -> list[str]:
    """Entfernt für jeden Issue-Auftrag mit Marke `_park:<skey>` die Marke, den Zähler `state[skey]` und
    `_versuch:<skey>`, wenn bereit_seit(skey) einen Zeitpunkt liefert, der NACH `state["_versuch:<skey>"]`
    liegt. Fehlt `_versuch:<skey>` (Zustand aus der Zeit vor dieser Änderung), bleibt die Marke
    (im Zweifel geparkt). Wirft bereit_seit, bleibt die Marke. Pro entfernter Marke eine Logzeile
    „<skey>: wieder auf bereit gesetzt, Parkmarke entfernt.“. Gibt die entfernten skey zurück.
    Speichert nicht (main speichert danach)."""
    entfernt: list[str] = []
    for key in list(state):
        if not key.startswith("_park:"):
            continue
        skey = key[len("_park:"):]
        raw_key = skey.split("/", 1)[1] if "/" in skey else skey
        if not _ISSUE_JOB_KEY_RE.match(raw_key):
            continue
        versuch_ts = state.get(f"_versuch:{skey}")
        if versuch_ts is None:
            continue
        try:
            b_ts = bereit_seit(skey)
        except Exception:
            continue
        if b_ts is not None and b_ts > versuch_ts:
            state.pop(key, None)
            state.pop(skey, None)
            state.pop(f"_versuch:{skey}", None)
            log(f"{skey}: wieder auf bereit gesetzt, Parkmarke entfernt.")
            entfernt.append(skey)
    return entfernt


def _claim_name(skey: str) -> str:
    """Dateiname zu einem Auftragsschlüssel (mit Projektvorsatz): unerlaubte Zeichen werden „_“."""
    return _CLAIM_UNSAFE_RE.sub("_", skey)


def claim_path(skey: str) -> str:
    """Pfad der Belegungsdatei CLAIM_DIR/<Name>.claim; legt nichts an."""
    return os.path.join(CLAIM_DIR, _claim_name(skey) + ".claim")


def _claim_done_path(skey: str) -> str:
    """Pfad der „erledigt“-Marke CLAIM_DIR/<Name>.done; legt nichts an."""
    return os.path.join(CLAIM_DIR, _claim_name(skey) + ".done")


def _claim_owner(path: str) -> int | None:
    """PID aus einer Belegungsdatei; None, wenn sie fehlt oder unlesbar ist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    """True, wenn der Prozess noch läuft. Ein fehlendes Recht bedeutet: er lebt (anderer Nutzer)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill(pid, 0) ist unter Windows kein „nur prüfen“: Signal 0 ist dort CTRL_C_EVENT und
        # trifft die ganze Konsole samt pytest-Sitzung. Deshalb fragt der Windows-Zweig die
        # Win32-API, ohne os.kill (#118).
        import ctypes

        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return k.GetLastError() == 5             # Zugriff verweigert: der Prozess lebt
        try:
            code = ctypes.c_ulong()
            k.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            k.CloseHandle(handle)                    # immer schließen, auch bei Fehler
        return code.value == 259                     # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def cleanup_claims(max_age_days: int = CLAIM_MAX_AGE_DAYS, now: float | None = None) -> int:
    """Löscht in CLAIM_DIR veraltete Dateien und gibt zurück, wie viele es waren.

    Gelöscht wird eine Datei, deren Änderungszeit mehr als max_age_days Tage vor `now` liegt
    (`now` in Sekunden seit der Epoche; Vorgabe time.time()), und zwar nur:
    - eine Datei mit Endung `.done`, immer;
    - eine Datei mit Endung `.claim`, wenn ihr Besitzer nicht mehr lebt: die PID in der Datei ist
      nicht lesbar oder `_pid_alive(pid)` ist False. Eine alte `.claim`-Datei eines lebenden
      Prozesses bleibt.
    Alles andere bleibt: Dateien mit anderer Endung, Unterverzeichnisse, junge Dateien.
    Fehlt CLAIM_DIR, ist das Ergebnis 0 und das Verzeichnis wird nicht angelegt. Schlägt das Löschen
    einer Datei mit OSError fehl, wird sie übersprungen und nicht mitgezählt. Wirft nie.
    """
    jetzt = time.time() if now is None else now
    grenze = jetzt - max_age_days * 86400
    entfernt = 0
    try:
        eintraege = list(os.scandir(CLAIM_DIR))
    except OSError:
        return 0
    for eintrag in eintraege:
        try:
            if not eintrag.is_file() or eintrag.stat().st_mtime >= grenze:
                continue
            if eintrag.name.endswith(".claim"):
                pid = _claim_owner(eintrag.path)
                if pid is not None and _pid_alive(pid):
                    continue
            elif not eintrag.name.endswith(".done"):
                continue
            os.remove(eintrag.path)
            entfernt += 1
        except OSError:
            continue            # gesperrt oder inzwischen weg: nicht zählen, Lauf nicht abbrechen
    return entfernt


def claimed_elsewhere(skey: str) -> bool:
    """True, wenn ein anderer lebender Prozess den Auftrag belegt hat (#59).

    Ein beendeter Slot blockiert nicht: claim_job() übernimmt dessen Belegung.
    """
    owner = _claim_owner(claim_path(skey))
    return owner is not None and owner != os.getpid() and _pid_alive(owner)


def claim_job(skey: str) -> bool:
    """Belegt den Auftrag atomar für diesen Prozess (Datei mit der PID) und meldet Erfolg.

    True: neu angelegt, schon dem eigenen Prozess gehörend oder von einem beendeten Prozess
    übernommen. False: ein anderer lebender Slot hält ihn. Ohne die Belegung nahmen beide Slots
    denselben Nacharbeits-Auftrag an (#59) – das kostet Modellläufe und MAX_TRIES, und zwei
    Commits auf demselben Branch drohen zu konkurrieren.

    Ist CLAIM_DIR nicht nutzbar, wird einmal eine „⚠ Stephan:“-Zeile geschrieben (Regel 7) und
    True geliefert, damit der Takt nicht stillsteht. Dann ist die Doppelbearbeitung wieder
    möglich – sichtbar, aber nicht verhindert.
    """
    path = claim_path(skey)
    try:
        os.makedirs(CLAIM_DIR, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if claimed_elsewhere(skey):
                return False
            # Eigene oder verwaiste Belegung (abgebrochener Takt): übernehmen.
            fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except OSError as exc:
        if CLAIM_DIR not in _CLAIM_WARNED:
            _CLAIM_WARNED.add(CLAIM_DIR)
            log(f"⚠ Stephan: Auftrags-Belegung {CLAIM_DIR} nicht nutzbar "
                f"({type(exc).__name__}: {exc}) – Doppelbearbeitung wieder möglich.")
        return True


def release_job(skey: str) -> None:
    """Löscht die Belegung, aber nur wenn sie dem eigenen Prozess gehört; fehlt sie, kein Fehler.

    Die Eigentumsprüfung verhindert, dass ein Slot die Belegung eines anderen wieder freigibt.
    """
    path = claim_path(skey)
    if _claim_owner(path) != os.getpid():
        return
    try:
        os.remove(path)
    except OSError:
        pass            # schon weg oder nicht lesbar: die Belegung ist fort


def mark_job_done(skey: str) -> None:
    """Merkt die angekommene Nacharbeit für alle Slots (#59): Datei CLAIM_DIR/<Name>.done.

    Die Liste DONE in der Zustandsdatei bleibt je Slot; sie allein sah den anderen Slot nicht,
    und dieselbe Nacharbeit lief danach erneut an.
    """
    try:
        os.makedirs(CLAIM_DIR, exist_ok=True)
        with open(_claim_done_path(skey), "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        log(f"Warnung: „erledigt“ für {skey} nicht merkbar ({type(exc).__name__}: {exc}).")


def job_done_anywhere(skey: str) -> bool:
    """True, wenn irgendein Slot diese Nacharbeit schon angekommen gemeldet hat (#59)."""
    return os.path.exists(_claim_done_path(skey))


def _iso_sekunden(text: object) -> float | None:
    """Sekunden seit der Epoche aus einem gh-Zeitstempel (ISO 8601), sonst None.

    Ohne Zeitzone gilt UTC; ein unlesbarer Wert zählt als unbekannt und wird nie geraten."""
    if not isinstance(text, str):
        return None
    try:
        zeit = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=timezone.utc)
    return zeit.timestamp()


def verwaiste_auftraege(issues: list[dict], prs: list[dict], now: float | None = None) -> list[int]:
    """Nummern der verwaisten Issues, aufsteigend.

    `issues`: Einträge von `gh issue list --label in-arbeit` mit `number` und `updatedAt`
    (ISO 8601). `prs`: offene PRs mit `headRefName`. Verwaist heißt: seit mindestens VERWAIST_S
    Sekunden unverändert, kein PR auf `issue_branch(N)` und keine lebende Belegung
    (`claim_path(PREFIX + f"issue{N}")`). Ein Eintrag ohne lesbares `updatedAt` zählt nicht.
    Wirft nie, ändert nichts, schreibt kein Log. `now` Vorgabe: jetzt_s()."""
    jetzt = jetzt_s() if now is None else now
    branches = {pr.get("headRefName") for pr in prs or [] if isinstance(pr, dict)}
    verwaist: list[int] = []
    for eintrag in issues or []:
        if not isinstance(eintrag, dict):
            continue
        try:
            nummer = int(eintrag.get("number"))
        except (TypeError, ValueError):
            continue
        geaendert = _iso_sekunden(eintrag.get("updatedAt"))
        if geaendert is None or jetzt - geaendert < VERWAIST_S:
            continue
        if issue_branch(nummer) in branches:
            continue
        # Eine tote Belegung (abgebrochener Takt) hält den Auftrag nicht; eine lebende schon.
        besitzer = _claim_owner(claim_path(PREFIX + f"issue{nummer}"))
        if besitzer is not None and _pid_alive(besitzer):
            continue
        verwaist.append(nummer)
    return sorted(verwaist)


def verwaiste_melden(state: dict, now: float | None = None) -> list[int]:
    """Meldet verwaiste Issues (`in-arbeit` ohne Lauf und ohne PR) einmal je Issue (#183).

    Nur der erste Slot fragt GitHub und schreibt (sonst käme jede Meldung doppelt). Je neuem
    verwaisten Issue entsteht genau eine „⚠ Stephan:“-Zeile; der Zustandsschlüssel
    `VERWAIST + N` verhindert die Wiederholung. Ist ein Issue nicht mehr verwaist, wird sein
    Schlüssel entfernt — wird es später wieder verwaist, meldet der Takt erneut. Ein
    gescheiterter gh-Aufruf hinterlässt nur eine Warnung ohne „⚠“ und ändert nichts.
    Rückgabe: die Nummern, die in diesem Aufruf neu gemeldet wurden."""
    if WORKER_SLOT not in ("", "1"):
        return []
    jetzt = jetzt_s() if now is None else now
    try:
        issues = gh("issue", "list", "--state", "open", "--label", "in-arbeit",
                    "--json", "number,updatedAt")
        prs = gh("pr", "list", "--state", "open", "--json", "number,headRefName")
    except Exception as e:
        # Regel 7: sichtbar, aber ohne „⚠“ — der Zustand bleibt unverändert.
        log(f"Warnung: Prüfung auf verwaiste Aufträge fehlgeschlagen ({type(e).__name__})")
        return []
    verwaist = verwaiste_auftraege(issues or [], prs or [], jetzt)
    gemeldet: list[int] = []
    geaendert = False
    for nummer in verwaist:
        key = VERWAIST + str(nummer)
        if state.get(key):
            continue
        stand = None
        for eintrag in issues or []:
            if isinstance(eintrag, dict) and str(eintrag.get("number")) == str(nummer):
                stand = _iso_sekunden(eintrag.get("updatedAt"))
                break
        minuten = 0 if stand is None else int((jetzt - stand) // 60)
        alarm("-", f"Issue #{nummer} steht seit {minuten} Minuten auf in-arbeit, "
                   "ohne Lauf und ohne PR; Label prüfen")
        state[key] = True
        geaendert = True
        gemeldet.append(nummer)
    for key in [k for k in state if k.startswith(VERWAIST)]:
        try:
            nummer = int(key[len(VERWAIST):])
        except ValueError:
            nummer = None
        if nummer not in verwaist:
            del state[key]
            geaendert = True
    if geaendert:
        save_state(state)
    return gemeldet


def main_red_sha() -> str | None:
    """Kurz-Hash (7 Zeichen) des Commits, auf dem der jüngste abgeschlossene Lauf des Workflows
    `tests` auf `main` rot war; sonst None.

    Rot ist nur conclusion == "failure". Erfolg, abgebrochen, übersprungen, leere Liste, None oder
    ein Wert ohne die beiden Felder ergeben None. Wirft nie: scheitert der Aufruf, steht die Zeile
    "Warnung: Zustand von main nicht abfragbar (<Klasse>)." im Log (nie der Text der Ausnahme,
    Regel 5) und das Ergebnis ist None. Absichtlich nicht „unbekannt = blockieren“ wie bei
    Regel 8: Ein defektes `gh run list` darf die Pipeline nicht stilllegen.
    """
    try:
        runs = gh("run", "list", "--workflow", "tests", "--branch", "main",
                  "--status", "completed", "--limit", "1", "--json", "conclusion,headSha")
    except Exception as e:
        log(f"Warnung: Zustand von main nicht abfragbar ({type(e).__name__}).")
        return None
    if not isinstance(runs, list) or not runs:
        return None
    run = runs[0]
    if not isinstance(run, dict) or run.get("conclusion") != "failure":
        return None
    sha = run.get("headSha")
    if not isinstance(sha, str) or not sha:
        return None
    return sha[:7]


def main_is_red(state: dict) -> bool:
    """True, wenn main_red_sha() einen Hash liefert.

    Beim ersten Mal je Hash schreibt die Funktion eine Logzeile, die mit dem Zeitstempel und danach
    "⚠ Stephan:" beginnt und den Hash und das Wort "main" enthält (Regel 7), und merkt den Hash im
    Zustand unter dem Schlüssel f"_mainrot:{REPO}" (log_on_change, speichert den Zustand). Derselbe Hash
    erzeugt keine weitere Zeile. Ist main nicht (mehr) rot, wird der Schlüssel gelöscht und der Zustand
    gespeichert, sodass ein späteres Rot wieder gemeldet wird.
    """
    sha = main_red_sha()
    key = f"_mainrot:{REPO}"
    if not sha:
        if state.pop(key, None) is not None:
            save_state(state)
        return False
    log_on_change(state, key, sha,
                  f"⚠ Stephan: Workflow tests ist auf main rot (Commit {sha}); keine neuen Issue-Aufträge.")
    return True


def find_job(state: dict):
    """Erster Auftrag, dessen Schlüssel weder geparkt noch von einem anderen Slot belegt oder
    erledigt ist, oder None.

    Ein liegengebliebener Auftrag darf den Takt und die weiteren Projekte nicht blockieren.
    Die Belegung gehört allen Slots zusammen (#59), sonst nahmen beide denselben Auftrag an.
    Ist `main` rot, entfallen neue Issue-Aufträge: Ihre PRs wären ohne Schuld des Workers rot
    (#168). Nacharbeit und PR-Text laufen weiter, denn eine Nacharbeit kann die Reparatur sein.
    """
    def skip(key: str) -> bool:
        skey = PREFIX + key
        return job_parked(state, key) or claimed_elsewhere(skey) or job_done_anywhere(skey)

    job = rework_job(project_done(state), skip) or text_fix_job(skip)
    if job:
        return job
    # issue_job erhält den Takt-Zustand (#206). Ältere, eingefrorene Akzeptanztests
    # (#59) mocken issue_job per lambda skip=None; Signatur vorab per inspect prüfen,
    # damit keine echten Fehler im Rumpf von issue_job verschluckt werden (Regel 7).
    try:
        params = inspect.signature(issue_job).parameters
        takes_state = "state" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    except (ValueError, TypeError):
        takes_state = True
    job = issue_job(skip, state=state) if takes_state else issue_job(skip)
    if job and main_is_red(state):
        return None
    return job


def ordered_projects(
    projects_list: list["projects.Project"],
    last_name: str | None,
) -> list["projects.Project"]:
    """Gibt die Projektliste zyklisch rotiert zurück, sodass das Projekt unmittelbar nach
    `last_name` an erster Stelle steht.

    Ist `last_name` None, leer oder nicht in `projects_list` vorhanden, oder besteht die
    Liste aus weniger als 2 Einträgen, wird eine unveränderte Kopie der Liste zurückgegeben.
    Reine Hilfsfunktion ohne I/O.

    Warum (#226): Mit fester Reihenfolge bekam immer `PROJECTS[0]` den Takt; ein dauerhaft
    volles erstes Projekt hungerte alle weiteren aus."""
    if not last_name or len(projects_list) < 2:
        return list(projects_list)
    namen = [p.name for p in projects_list]
    if last_name not in namen:
        return list(projects_list)
    i = namen.index(last_name)
    return list(projects_list[i + 1:]) + list(projects_list[:i + 1])


def pick_job(state: dict):
    """Erste Arbeit der Projekte in Fair-Share-Reihenfolge; None, wenn nirgends Arbeit liegt.

    Fair-Share (#226): `state["_last_project"]` merkt das Projekt, für das zuletzt ein Auftrag
    angenommen wurde. `ordered_projects()` stellt das Projekt danach an den Anfang, damit ein
    dauerhaft volles erstes Projekt die übrigen nicht mehr aushungert. Hat das bevorzugte
    Projekt keine fällige Arbeit, folgen die übrigen in rotierter Reihenfolge (kein Leerlauf).
    Mit nur einem Projekt unverändert find_job(). Ein Projekt, dessen Klon fehlt, wird einmal
    gemeldet und übersprungen. Nach einem Treffer bleibt dieses Projekt aktiv (Globalen), und
    `_last_project` wird zusammen mit dem Zustand gesichert."""
    if len(PROJECTS) == 1:
        return find_job(state)
    for p in ordered_projects(PROJECTS, state.get("_last_project")):
        if not os.path.isdir(p.workdir):
            log_on_change(state, f"_fehlt:{p.name}", "fehlt",
                          f"Projekt {p.name}: Klon {p.workdir} fehlt, übersprungen (install.sh ausführen).")
            continue
        # PREFIX gehört zum ersten Projekt der Liste (alte Zustandsschlüssel), nicht zur
        # Position in der rotierten Reihenfolge.
        use_project(p, first=(p == PROJECTS[0]))
        job = find_job(state)
        if job:
            state["_last_project"] = p.name
            save_state(state)
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


def pre_push_hook(test_cmd: str) -> str:
    """Text des pre-push-Hooks: blockiert den Push, solange die Tests rot sind (#68).

    Absichtlich sh ohne Zusatzbefehle: Der Hook läuft beim `git push` des Agenten und muss
    ohne Netz, ohne gh und mit jedem git auf dem Server funktionieren. `--no-verify` steht
    hier bewusst nicht, weil die Umgehung nur der Eskalation im Auftrag erlaubt ist.

    Die GIT_*-Variablen werden vor dem Testbefehl zurückgesetzt: Beim Push aus einem Worktree
    (Slots w1/w2) setzt git im Hook GIT_DIR auf <klon>/.git/worktrees/<name> und dazu GIT_PREFIX.
    Die Tests legen sonst ihre Wegwerf-Repos im echten Klon an statt im tmp_path, und `git commit`
    hinterlässt einen Fremdcommit im gepushten Branch (#212). Das `unset` gilt nur im Hook-Prozess;
    der laufende `git push` behält seine Umgebung.
    """
    cmd = shlex.quote(test_cmd)
    return (
        "#!/bin/sh\n"
        "# Erzeugt von worker_tick.ensure_pre_push_hook(): kein Push mit roten Tests (#68).\n"
        "unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY"
        " GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_QUARANTINE_PATH\n"
        'env -i PATH="$PATH" HOME="$HOME" AGENT_PROJECTS=/nonexistent/agent-projects.json '
        f'AGENT_SETTINGS=/nonexistent/agent-settings.json sh -c {cmd}\n'
        "if [ $? -ne 0 ]; then\n"
        "    echo \"pre-push: Tests fehlgeschlagen, Push abgelehnt."
        " Fehler beheben, nicht umgehen.\" >&2\n"
        "    exit 1\n"
        "fi\n"
        "exit 0\n")


def install_pre_push_hook(hooks_dir: str, test_cmd: str) -> str:
    """Schreibt den pre-push-Hook nach hooks_dir und gibt seinen Pfad zurück (#68).

    Wird bei jedem Lauf neu geschrieben, damit eine geänderte TEST_CMD oder ein gelöschter
    Hook nicht still wirkungslos bleibt.
    """
    os.makedirs(hooks_dir, exist_ok=True)
    path = os.path.join(hooks_dir, "pre-push")
    # Der Hook-Ordner gehört allen Slots eines Klons: Erst in eine eigene Datei schreiben und dann
    # ersetzen, sonst führt ein pushender Slot einen halb geschriebenen Hook aus.
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(pre_push_hook(test_cmd))
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return path


def git_hooks_dir(cwd: str) -> str:
    """Ermittelt das Hook-Verzeichnis, das git in cwd benutzt (#68).

    Im Worktree-Modus ist das der gemeinsame Hook-Ordner des Basis-Klons; ihn zu berechnen,
    statt `.git/hooks` zu raten, hält Hook und Repo zusammen (git-path liefert je nach
    Version einen relativen oder absoluten Pfad).
    """
    out = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=cwd,
                         capture_output=True, text=True, check=True, env=ENV)
    path = out.stdout.strip()
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def ensure_pre_push_hook() -> str | None:
    """Schreibt den pre-push-Hook in den Klon des Slots und gibt seinen Pfad zurück (#68).

    main() ruft das nach prepare_workdir() und vor dem Agenten auf, bei jedem Lauf. Es gehört nicht
    in prepare_workdir(): Dessen Aufruffolge (nur git fetch/worktree/checkout/reset/clean, kein
    Schreibzugriff) ist durch test_issue_40 festgeschrieben. Das Hook-Verzeichnis kommt von git,
    nicht aus einer Vermutung (siehe git_hooks_dir). Ein Fehler bricht den Lauf ab (Regel 7), statt
    den Agenten ungeschützt starten zu lassen. Gibt es WORKDIR nicht, ist nichts zu schützen: das
    kommt nach einem erfolgreichen prepare_workdir() nicht vor, nur in Tests mit gefaktem Aufruf.
    """
    if not os.path.isdir(WORKDIR):
        return None
    return install_pre_push_hook(git_hooks_dir(WORKDIR), TEST_CMD)


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

    # Worktree anlegen oder bereinigen — detached an origin/main, nicht auf dem Branch `main`.
    # Git erlaubt einen Branch nur an einer Stelle, und mehrere Slots teilen sich diesen Klon:
    # `worktree add -B main` bzw. `checkout -qf main` scheitern, sobald der andere Slot `main`
    # hält (exit 255/128), und der Takt stirbt mit CalledProcessError, nachdem er das Issue
    # schon übernommen hat (14.10.: „fatal: 'main' is already checked out at …/w2“, Issue #40).
    # install-lib.sh hält den Basis-Klon aus demselben Grund detached (Zeilen 47–49, 61).
    if not os.path.exists(WORKDIR):
        os.makedirs(os.path.dirname(WORKDIR), exist_ok=True)
        subprocess.run(["git", "worktree", "add", "--force", "--detach", WORKDIR, "origin/main"],
                       cwd=base, check=True, env=ENV)
    else:
        for cmd in (["git", "checkout", "-qf", "--detach", "origin/main"],
                    ["git", "reset", "-q", "--hard", "origin/main"],
                    ["git", "clean", "-qfd"]):
            subprocess.run(cmd, cwd=WORKDIR, check=True, env=ENV)


_ECHTE_PREPARE_WORKDIR = prepare_workdir
_ECHTE_SANDBOX_HEALTH_CHECK = sandbox_health_check


def prepare_issue_frame(issue_no: int) -> Path | None:
    """Richtet für `issue_no` nach `prepare_workdir()` und `ensure_pre_push_hook()` im aktuellen
    `WORKDIR` deterministisch den Branch `feature/issue-<issue_no>` und (falls vorhanden) den
    ersten Commit mit `tests/acceptance/test_issue_<issue_no>.py` über
    `worker_frame.setup_issue_branch` ein.

    Existiert `WORKDIR` nicht (`not os.path.isdir(WORKDIR)`), tut die Funktion nichts und gibt
    `None` zurück (damit bestehenden Tests mit gefaktem Arbeitsbaum keine zusätzlichen `gh`- oder
    `git`-Aufrufe entstehen). Sonst liest sie den Issue-Text über
    `gh("issue", "view", str(issue_no), "--json", "body")` und ruft
    `worker_frame.setup_issue_branch(WORKDIR, issue_no, body, run)` auf, wobei `run` den
    git-Befehl in `WORKDIR` mit `ENV` ausführt. Bei geschriebenem Akzeptanztest loggt sie
    `issue<issue_no>: Akzeptanztest vorab als Commit gesichert.`; Rückgabe ist der Rückgabewert
    von `setup_issue_branch` (Pfad der Datei oder `None` ohne Block)."""
    if not os.path.isdir(WORKDIR):
        return None
    data = gh("issue", "view", str(issue_no), "--json", "body")
    body = data.get("body") if isinstance(data, dict) else None

    def run(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=WORKDIR, env=ENV, check=True)

    path = worker_frame.setup_issue_branch(WORKDIR, issue_no, body, run)
    if path is not None:
        log(f"issue{issue_no}: Akzeptanztest vorab als Commit gesichert.")
    return path


def main() -> int:
    global LAST_AGENT_ERROR, LAST_OPENED_PR
    global AGENT_INFRA_FEHLER
    skey = "-"          # Regel 7: vor der Auftragswahl meldet der Takt „-“
    gh_fehler = False   # True: der Lauf endete in der Ausnahmebehandlung, _gherr bleibt gemerkt
    try:
        # #170: einmal je Lauf veraltete Belegungen aufräumen, bevor Arbeit gewählt wird.
        aufgeraeumt = cleanup_claims()
        if aufgeraeumt:
            log(f"{aufgeraeumt} alte Belegungsdateien aufgeräumt.")
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
                litellm_down(state)
                return 0
            litellm_up(state)
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
        # #183: Ein Issue mit `in-arbeit` ohne Lauf und ohne PR steht still; der erste Slot meldet
        # es einmal. Die Prüfung darf den Lauf nie abbrechen.
        try:
            verwaiste_melden(state)
        except Exception as e:
            log(f"Warnung: Prüfung auf verwaiste Aufträge fehlgeschlagen ({type(e).__name__})")
        # #187: Einen erledigten Auftrag (PR gemergt/geschlossen, Issue geschlossen) nicht ewig
        # als „wartet auf Stephan“ führen. Vor der Wahl, damit der Zustand danach sauber ist.
        entfernt = prune_parked(state, job_still_open)
        if entfernt:
            save_state(state)
            for erledigt in entfernt:
                log(f"{erledigt}: erledigt, Parkmarke entfernt.")
        entparkt = entparken(state, label_bereit_seit)
        if entparkt:
            save_state(state)
        job = pick_job(state)
        if not job:
            # #178: Ohne Auftrag rechnet der Takt den offenen Lauf ab — /key/info liefert dabei
            # den Spend, den LiteLLM inzwischen gebucht hat. Ohne offenen Lauf keine Abfrage.
            if state.get("_kosten_offen"):
                settle_pending(state, key_info())
            log_on_change(state, LAST_WORK, "keine", "Keine Arbeit.")
            parked = parked_job(state)
            # #218: Wartet etwas (übersprungen oder geparkt), ist die Untätigkeit ein Stillstand;
            # nach einer Stunde nennt der erste Slot die Gründe genau einmal (Regel 7).
            gruende = list(SKIPPED)
            if parked:
                gruende.append(f"{parked}: geparkt (wartet auf Stephan)")
            stillstand_vorher = state.get(STILLSTAND)
            gemeldet = stillstand_pruefen(state, gruende)
            if gemeldet or state.get(STILLSTAND) != stillstand_vorher:
                save_state(state)
            slot_status("idle", project=PROJECT, message=idle_message(parked, SKIPPED))
            return 0
        if state.pop(STILLSTAND, None) is not None:
            save_state(state)   # #218: Es läuft wieder Arbeit, der Stillstand endet
        key, task, pr_no, before = job
        skey = PREFIX + key          # im Zustand und im Log mit Projektvorsatz (beim ersten Projekt leer)
        if BACKEND != "litellm" and not ollama_free(
                status, say=lambda m: log_on_change(state, LAST_WORK, f"{skey} belegt", m)):
            return 0
        spend_vorher: float | None = None
        if BACKEND == "litellm":
            # #90: Zuerst das Tagesbudget des eigenen Keys. Ein leerer Key beantwortet auch die
            # Ausweichmodelle mit 429; ohne diese Prüfung verbrauchte der Lauf einen Versuch und
            # der Auftrag stünde nach MAX_TRIES bei Stephan. Ohne Arbeit kostet die Abfrage
            # nichts, und ohne Versuch läuft der Auftrag nach dem Reset von selbst weiter.
            info = key_info()
            # #178: Dieselbe Antwort rechnet den vorigen Lauf ab (LiteLLM bucht gebündelt) und
            # liefert den Startwert für diesen. Keine zusätzliche Abfrage.
            spend_vorher = spend_of(info)
            settle_pending(state, info)
            problem = budget_problem(info)
            if problem:
                reset = str((info or {}).get("budget_reset_at") or "unbekannt")[:16]
                log_on_change(state, "_budget", reset,
                              f"⚠ Stephan: {problem}, warte. Versuch zählt nicht.")
                return 0
            if state.pop("_budget", None) is not None:
                save_state(state)   # Budget wieder frei: die nächste Knappheit wird erneut gemeldet
            # Erst hier prüfen: ohne Arbeit kostet der Takt keine Anfrage. Vor dem Zähler, damit ein
            # gestörter Anbieter keine Versuche verbraucht.
            primary = model_for_attempt(state.get(skey, 0))
            try:
                model, failed = choose_model(primary)
            except TypeError:
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
        if not claim_job(skey):
            # #59: Der andere Slot hält den Auftrag. Eine Zeile je Wechsel, kein Versuch verbraucht.
            log_on_change(state, LAST_WORK, f"{skey} belegt",
                          f"{skey}: von einem anderen Slot belegt, warte.")
            return 0
        state[LAST_WORK] = skey

        n = job_issue_number(key)
        # #243: Vor dem Claim prüfen, ob der Agent im Arbeitsordner überhaupt arbeiten kann. Ein
        # defektes Verzeichnis würde den Lauf sofort mit EACCES beenden; ohne Claim entsteht
        # keine Phantom-Sperre. Geprüft wird nur, wenn der Agent wirklich unter AGENT_USER startet
        # (also der Takt als root läuft) und WORKDIR schon existiert: `prepare_workdir` legt es im
        # Betrieb erst danach an, und Tests faken es (conftest.py); den Rest fängt der Crash-Zweig.
        check_user, _ = agent_identitaet()
        check_aktiv = (prepare_workdir is _ECHTE_PREPARE_WORKDIR
                       or sandbox_health_check is not _ECHTE_SANDBOX_HEALTH_CHECK)
        if (n is not None and check_user and os.path.isdir(WORKDIR) and check_aktiv
                and not sandbox_health_check(WORKDIR, check_user)):
            log(f"⚠ Stephan: {skey}: Sandbox-Check für {WORKDIR} fehlgeschlagen, kein Claim. "
                "Versuch zählt nicht.")
            return 0
        if n is not None and not claim_issue(n):
            return 0

        # #215: Bis hierher lag die Modellprobe. Ist der PR inzwischen gemergt/geschlossen oder
        # fremd gepusht, wäre die Nacharbeit überholt – vor dem Zähler prüfen, damit sie keinen
        # Versuch verbraucht.
        if pr_no is not None and before is not None:
            ok, reason = verify_rework_pr(pr_no, before[0])
            if not ok:
                log(f"{skey}: Nacharbeit übersprungen ({reason})")
                slot_status("idle", project=PROJECT)
                return 0

        versuch_zaehlen(state, skey)
        save_state(state)
        # Jeder Lauf startet auf sauberem main; Reste eines abgebrochenen Laufs stören sonst.
        prepare_workdir()
        ensure_pre_push_hook()   # #68: vor dem Agenten, damit schon sein erster Push geprüft wird
        if n is not None and os.path.isdir(WORKDIR):
            # #234: Branch und Akzeptanztest-Commit legt der deterministische Rahmen aus
            # worker_frame.py an; ohne Akzeptanztest-Block startet kein Agent. Gibt es WORKDIR
            # nicht (gefaktes prepare_workdir in bestehenden Tests), entfällt die Vorbereitung.
            if prepare_issue_frame(n) is None:
                alarm(skey, "Akzeptanztest-Block im Issue nicht lesbar")
                slot_status("idle", project=PROJECT)
                return 0
            # #208: Ein fortgesetzter Branch kann auf einem alten main stehen; der Pre-Push-Hook
            # würde sonst wegen längst behobener Tests jeden Push ablehnen.
            sync_issue_branch_with_main(n)

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
        # Slot-Telemetrie (#14): running vor dem Lauf, idle danach. Der Lebenszyklus ist so in HA
        # sichtbar; write_slot_status bricht den Takt nie ab (slot_status fängt OSError).
        info: dict[str, object] = {"model": ACTIVE_MODEL, "agent": AGENT, "project": PROJECT,
                                   "started_at": time.strftime("%H:%M:%S"), "pid": os.getpid()}
        issue_match = re.match(r"issue(\d+)", key)
        if issue_match:
            info["issue"] = int(issue_match.group(1))
            info["branch"] = issue_branch(int(issue_match.group(1)))
        if pr_no is not None:
            info["pr"] = pr_no
        slot_status("running", **info)
        # #178: Anfang und Ende jedes Laufs ins Audit-Log. Die Kosten selbst schreibt erst der
        # nächste Takt (settle_pending): LiteLLM bucht gebündelt, direkt nach dem Lauf fehlt
        # sonst der letzte Teil.
        lauf_start, begonnen = datetime.now(timezone.utc), time.monotonic()
        audit("run_started", key=skey, spend_vorher=spend_vorher)
        LAST_AGENT_ERROR = None   # log_stream_line setzt ihn nur bei einem Anbieterfehler
        AGENT_INFRA_FEHLER = None  # #243: nur die Ausgabe dieses Laufs zählt
        rc = run_agent(prompt)
        usage = LAST_AGENT_USAGE   # run_agent() liefert nur den Exit-Code; Usage darüber (#222)
        lauf_ende, dauer = datetime.now(timezone.utc), round(time.monotonic() - begonnen, 1)
        if rc == 124:
            log(f"{skey}: nach {RUN_TIMEOUT // 60} min abgebrochen.")
        else:
            log(f"{skey}: {name} beendet (Exit {rc}).")
        slot_status("idle", message="Takt beendet", project=PROJECT)
        audit("run_finished", key=skey, exit_code=rc, dauer_s=dauer,
              anbieterfehler=bool(LAST_AGENT_ERROR))
        # agent_run_finished für Dashboard (#222): Issue, Modell, Tokens, Cost, Exit-Code
        issue_no = job_issue_number(key)
        audit("agent_run_finished", issue=issue_no, model=ACTIVE_MODEL, exit_code=rc,
              tokens=usage, cost=usage.get("cost") if isinstance(usage, dict) else None)
        state["_kosten_offen"] = {"key": skey, "slot": WORKER_SLOT, "spend_vorher": spend_vorher,
                                  "start": lauf_start.isoformat(), "ende": lauf_ende.isoformat()}
        save_state(state)
        # #243: Ein Rechte- oder Laufzeitfehler der Sandbox (EACCES, ENOSPC, fehlendes Modul) sagt
        # nichts über den Auftrag aus: Der Versuch wird zurückgenommen und der Claim geht sofort
        # zurück, statt 45 Minuten über Regel 8 alle Folgeaufträge zu sperren. Nur ein
        # abgebrochener Lauf zählt; ein erfolgreicher Lauf mit „permission denied“ in der
        # Testausgabe bleibt ein regulärer Befund.
        grund = redact(AGENT_INFRA_FEHLER or "") if rc != 0 else ""
        if grund:
            state[skey] = max(0, state.get(skey, 0) - 1)
            save_state(state)
            log(f"{skey}: Infrastrukturfehler im Lauf ({grund}), Versuch zählt nicht.")
            if n is not None:
                release_failed_claim(n, grund)
            return 0
        err = LAST_AGENT_ERROR
        if err:
            # Scheitert der Lauf schon am Anbieter (429/401/5xx), ist das nicht die Schuld des
            # Auftrags: Der Versuch wird zurückgenommen, sonst läge er nach MAX_TRIES bei Stephan
            # (#106, Beleg 15.10.). Denselben Fehler je Schlüssel nur einmal melden.
            state[skey] = max(0, state.get(skey, 0) - 1)
            save_state(state)
            log(f"{skey}: Anbieterfehler im Lauf ({err}), Versuch zählt nicht.")
            akey = f"_apierr:{skey}"
            if state.get(akey) != err:
                alarm(skey, f"Anbieterfehler: {err}")
                state[akey] = err
                save_state(state)
            return 0
        if state.pop(f"_apierr:{skey}", None) is not None:
            save_state(state)   # ein früherer Anbieterfehler ist vorbei
        if n is not None:
            LAST_OPENED_PR = None   # setzt erst ein tatsächlich neu angelegter PR (#222)
            opened = open_pr(n)
            # pr_opened Event nur bei neu erstelltem PR (#222)
            if opened and LAST_OPENED_PR is not None:
                audit("pr_opened", issue=n, pr=LAST_OPENED_PR, branch=issue_branch(n))
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
                mark_job_done(skey)   # #59: die Nacharbeit ist in allen Slots erledigt
            else:
                log(f"WARNUNG {skey}: weder Commit noch PR-Text auf PR #{pr_no} geändert, "
                    "Nacharbeit nicht erledigt.")
        return 0
    except Exception as exc:   # der Timer darf nie mit Traceback enden (Regel 7)
        # Regel 5: an alarm() geht nur die erste Zeile der Meldung, nie die rohe, mehrzeilige
        # Ausnahme; die Kurzbeschreibung bleibt damit einzeilig. Einen bekannten gh-Grund
        # (#161) meldet report_gh_error je Slot nur einmal, bis ein Lauf ohne Fehler endet.
        gh_fehler = True
        try:
            state = load_state()
        except Exception:
            state = {}   # Zustandsdatei selbst kaputt: melden, aber nichts merken können
        vorher = state.get("_gherr")
        report_gh_error(state, skey, exc)
        if state.get("_gherr") != vorher:
            save_state(state)
        return 0
    finally:
        if not gh_fehler:
            # Ein Lauf ohne Fehler hebt die Merkung auf: ein späterer gh-Fehler wird wieder gemeldet.
            try:
                state = load_state()
                if clear_gh_error(state):
                    save_state(state)
            except Exception as e:
                log(f"Warnung: Zustand nicht lesbar oder schreibbar ({type(e).__name__}).")
        # #59: Die Belegung endet mit dem Lauf, auch wenn er geworfen hat. Ohne eigene Belegung
        # (keine Arbeit, fremde Belegung) ist das ein Nullaufruf.
        release_job(skey)


if __name__ == "__main__":
    sys.exit(main())
