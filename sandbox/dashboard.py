#!/usr/bin/env python3
"""Web-Dashboard der Agenten-Sandbox (löst das Home-Assistant-Dashboard ab).

Nur lesend und nur Standardbibliothek. Der Dienst fasst vorhandene Quellen zusammen:
Slot-Status (`/run/worker-slots/slot-<n>.json`, gelesen wie in `qwen_webhook.py`), Projekte,
Issues, PRs, „braucht-stephan“ und LiteLLM-Budgets (`ha_status.build_status`) und den Schwanz
von `/root/worker.log`. Die Seite holt den Stand über Server-Sent-Events (`/events`).

Endpunkte: `/` (Seite), `/api/state` (JSON), `/events` (SSE), `/healthz` und
`POST /api/budget` (Limit eines LiteLLM-Keys ändern, nur mit Schreib-Token).

Push-Meldungen über ntfy (`NTFY_URL`): siehe `dashboard_notify.py`.

Einzige Ausnahme vom „nur lesend“: das Budget-Limit. Dafür braucht der Dienst
`LITELLM_MASTER_KEY` und `DASHBOARD_WRITE_TOKEN` (beide nur in `/etc/agent-dashboard.env`, Modus 600).
Fehlt eines, ist das Ändern aus und die Seite zeigt keinen Knopf. Geändert wird nur `max_budget`.

Kein Login: Der Dienst gehört ins LAN und nie hinter eine Portfreigabe. Deshalb liefert er
nur aufbereitete Felder; Fehler erscheinen als Name der Exception-Klasse, Log-Zeilen laufen
durch `redact` (Regel 5: Geheimnisse nie ausgeben).

Einstellungen (Umgebung): DASHBOARD_BIND (Standard 0.0.0.0), DASHBOARD_PORT (8099),
DASHBOARD_STATUS_TTL (Sekunden, in denen `gh`/LiteLLM-Antworten wiederverwendet werden, 90),
WORKER_LOG, SLOT_DIR, LITELLM sowie die Budget-Keys aus `ha_status.py`.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # Module liegen nebenan
import dashboard_notify  # noqa: E402
import dashboard_stats  # noqa: E402
import ha_status  # noqa: E402
import projects  # noqa: E402
import qwen_webhook  # noqa: E402

LOG: str = os.environ.get("WORKER_LOG", "/root/worker.log")
SLOT_DIR: str = os.environ.get("SLOT_DIR", "/run/worker-slots")
BIND: str = os.environ.get("DASHBOARD_BIND", "0.0.0.0")
PORT: int = int(os.environ.get("DASHBOARD_PORT", "8099"))
STATUS_TTL: float = float(os.environ.get("DASHBOARD_STATUS_TTL", "90"))
NOTIFY_INTERVAL: float = float(os.environ.get("NOTIFY_INTERVAL", "30"))   # Sekunden zwischen zwei Push-Prüfungen
LOG_LINES = 400            # so viele Zeilen hält der Dienst vor
LOG_PRELOAD_BYTES = 64 * 1024
SSE_INTERVAL = 2.0         # Sekunden zwischen zwei Prüfungen je Verbindung
STEPHAN_MARK = "⚠ Stephan"
MIN_TOKEN_LEN = 16         # kürzere Schreib-Token gelten als nicht eingerichtet
MAX_BUDGET_LIMIT = 10000   # Obergrenze gegen Tippfehler (USD)
FAIL_DELAY = 1.0           # Sekunden Wartezeit nach falschem Token (bremst Raten)
DEAD_MESSAGE = "Prozess beendet"   # setzt qwen_webhook.read_slots für einen verwaisten Lauf

# Umgebungsvariablen, deren Werte nie im Dashboard erscheinen dürfen.
_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|WEBHOOK|URL)", re.IGNORECASE)


def redact(text: str, env: dict | None = None) -> str:
    """ANSI-Codes entfernen und die Werte geheimer Umgebungsvariablen maskieren."""
    text = qwen_webhook.strip_ansi(text)
    for name, value in (os.environ if env is None else env).items():
        if len(value) >= 8 and _SECRET_NAME_RE.search(name):
            text = text.replace(value, f"<{name}>")
    return text


class LogBuffer:
    """Hält die letzten Log-Zeilen mit laufender Nummer; liest neue Zeilen nach."""

    def __init__(self, path: str = LOG, limit: int = LOG_LINES, stats=None):
        self.path = path
        self.stats = stats
        self.lines: deque[tuple[int, str]] = deque(maxlen=limit)
        self.seq = 0
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._add(self._preload())
        self._follower = qwen_webhook.Follower(path)   # ab dem aktuellen Dateiende
        if stats is not None and self._follower.fh:
            stats.preload(path, self._follower.fh.tell())   # Statistik: weiter zurück, bis genau hierher

    def _preload(self) -> list[str]:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - LOG_PRELOAD_BYTES))
                text = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return []
        lines = [ln.rstrip("\r") for ln in text.split("\n")]
        if len(text) >= LOG_PRELOAD_BYTES:
            lines = lines[1:]   # erste Zeile ist angeschnitten
        return [ln for ln in lines if ln.strip()]

    def _add(self, new: list[str]) -> None:
        with self._lock:
            for ln in new:
                self.seq += 1
                self.lines.append((self.seq, redact(ln)))

    def poll(self) -> None:
        with self._poll_lock:   # mehrere SSE-Verbindungen teilen sich einen Follower
            new = self._follower.poll()
            if self.stats is not None:
                for ln in new:
                    self.stats.feed(ln)
            self._add(new)

    def since(self, seq: int) -> list[tuple[int, str]]:
        with self._lock:
            return [(n, ln) for n, ln in self.lines if n > seq]

    def stephan(self, limit: int = 10) -> list[str]:
        with self._lock:
            hits = [ln for _, ln in self.lines if STEPHAN_MARK in ln]
        return hits[-limit:]


class StatusCache:
    """Merkt sich den teuren Teil (gh, LiteLLM) für `ttl` Sekunden.

    Scheitert ein Neuladen als Ganzes, bleibt der alte Stand stehen und `veraltet` wird
    gesetzt; die Seite zeigt dann einen Hinweis statt eines leeren Bildschirms.
    """

    def __init__(self, builder: Callable[[], dict], ttl: float = STATUS_TTL,
                 clock: Callable[[], float] = time.monotonic):
        self.builder, self.ttl, self.clock = builder, ttl, clock
        self.value: dict | None = None
        self.loaded_at = 0.0
        self.veraltet = False
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """Beim nächsten `get` neu laden (z. B. nach einer Budget-Änderung)."""
        with self._lock:
            self.loaded_at = float("-inf")

    def get(self) -> tuple[dict | None, bool]:
        with self._lock:
            if self.value is None or self.clock() - self.loaded_at >= self.ttl:
                try:
                    self.value, self.veraltet = self.builder(), False
                except Exception:   # noqa: BLE001 — Dashboard bleibt bedienbar, Fehlerart steht nirgends
                    self.veraltet = True
                self.loaded_at = self.clock()
            return self.value, self.veraltet


class BudgetError(Exception):
    """Fehler beim Ändern eines Budgets; die Meldung ist für die Anzeige gedacht (nie Geheimnisse)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class BudgetWriter:
    """Setzt `max_budget` eines LiteLLM-Keys über `POST /key/update` mit dem Master-Key.

    Ohne Master-Key und ausreichend langen Schreib-Token ist `enabled` falsch und nichts
    lässt sich ändern. Fehlertexte nennen nur Status und Art, nie Keys, Token oder URLs.
    """

    def __init__(self, env: dict | None = None, update: Callable[[str, float], None] | None = None):
        env = dict(os.environ if env is None else env)
        self.token = env.get("DASHBOARD_WRITE_TOKEN", "")
        self.master = env.get("LITELLM_MASTER_KEY", "")
        self.base = env.get("LITELLM") or ha_status.DEFAULT_LITELLM
        self.keys = {alias: env[var] for var, alias in ha_status.BUDGET_KEYS if env.get(var)}
        self.update = update or self._litellm_update
        self.fail_delay = FAIL_DELAY

    @property
    def enabled(self) -> bool:
        return len(self.token) >= MIN_TOKEN_LEN and bool(self.master)

    def token_ok(self, supplied: str) -> bool:
        return self.enabled and hmac.compare_digest(supplied.encode("utf-8"), self.token.encode("utf-8"))

    def _litellm_update(self, key: str, max_budget: float) -> None:
        body = json.dumps({"key": key, "max_budget": max_budget}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base.rstrip('/')}/key/update", data=body, method="POST",
            headers={"Authorization": f"Bearer {self.master}", "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10).close()
        except urllib.error.HTTPError as exc:
            raise BudgetError(f"LiteLLM lehnt die Änderung ab (HTTP {exc.code})", 502) from None
        except Exception as exc:   # noqa: BLE001 — nur die Art des Fehlers, nie der Text (URL, Key)
            raise BudgetError(f"LiteLLM nicht erreichbar ({type(exc).__name__})", 502) from None

    def set_budget(self, alias: object, value: object) -> float:
        """Prüft Eingaben und setzt das Limit; Rückgabe ist der gesetzte Wert."""
        if not isinstance(alias, str) or alias not in self.keys:
            raise BudgetError("Unbekannter Key")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BudgetError("Limit muss eine Zahl sein")
        if not 0 < value <= MAX_BUDGET_LIMIT:
            raise BudgetError(f"Limit muss größer 0 und höchstens {MAX_BUDGET_LIMIT} sein")
        value = round(float(value), 2)
        self.update(self.keys[alias], value)
        return value


def default_builder(env: dict | None = None) -> dict:
    """Projekte, Issues, PRs und Budgets über dieselben Funktionen wie `ha_status.py`."""
    env = dict(os.environ if env is None else env)
    base = env.get("LITELLM") or ha_status.DEFAULT_LITELLM
    budgets = {}
    for var, alias in ha_status.BUDGET_KEYS:
        if env.get(var):
            budgets[alias] = ha_status._default_key_info(env[var], base)
    return ha_status.build_status(projects.load_projects(env=env), ha_status._gh_fetch,
                                  budgets, datetime.now(timezone.utc))


def _elapsed(started_at: str | None, now: datetime) -> int | None:
    """Laufzeit in Sekunden aus „HH:MM:SS“ (der Slot kennt nur die Uhrzeit, kein Datum)."""
    try:
        h, m, s = (int(x) for x in (started_at or "").split(":"))
    except ValueError:
        return None
    secs = (now.hour * 3600 + now.minute * 60 + now.second) - (h * 3600 + m * 60 + s)
    return secs if secs >= 0 else secs + 86400   # über Mitternacht


def slot_view(slot: str, data: dict, now: datetime) -> dict:
    """Ein Slot für die Seite: nur bekannte Felder, Laufzeit berechnet, Status vereinheitlicht."""
    status = data.get("status") if data.get("status") in ("running", "idle") else "unbekannt"
    view = {"slot": slot, "status": status,
            "tot": status == "idle" and data.get("message") == DEAD_MESSAGE}
    for key in ("message", "issue", "pr", "title", "branch", "model", "agent", "project",
                "started_at", "updated_at"):
        if data.get(key) is not None:
            view[key] = data[key]
    if status == "running":
        view["laufzeit_s"] = _elapsed(data.get("started_at"), now)
    return view


def _slot_sort(item: tuple[str, dict]) -> tuple[int, str]:
    slot = item[0]
    return (0, f"{int(slot):06d}") if slot.isdigit() else (1, slot)


def build_state(slots: dict[str, dict], status: dict | None, veraltet: bool,
                stephan_lines: list[str], now: datetime) -> dict:
    """Gesamtstand für `/api/state` und die SSE-Ereignisse (ohne Log-Zeilen)."""
    views = [slot_view(s, d, now) for s, d in sorted(slots.items(), key=_slot_sort)]
    projekte = (status or {}).get("projekte", [])
    issues = sum(sum(p.get("issues", {}).values()) for p in projekte)
    prs = sum(p.get("prs", {}).get("offen", 0) for p in projekte)
    alerts = [{"art": "slot", "text": f"Slot {v['slot']}: Prozess beendet, ohne sich abzumelden"}
              for v in views if v["tot"]]
    alerts += [{"art": "stephan", "text": ln} for ln in stephan_lines]
    return {
        "zeit": now.isoformat(timespec="seconds"),
        "slots": views,
        "kopf": {"aktive_slots": sum(v["status"] == "running" for v in views),
                 "offene_issues": issues, "offene_prs": prs},
        "projekte": projekte,
        "braucht_stephan": (status or {}).get("braucht_stephan", 0),
        "budgets": (status or {}).get("budgets", {}),
        "stand": (status or {}).get("zeit"),
        "veraltet": veraltet or status is None,
        "alerts": alerts,
    }


class Dashboard:
    """Bündelt Quellen und liefert Zustand sowie SSE-Rahmen; testbar ohne HTTP."""

    def __init__(self, cache: StatusCache, logbuf: LogBuffer, slot_dir: str = SLOT_DIR,
                 is_pid_alive=qwen_webhook.pid_is_active,
                 clock: Callable[[], datetime] = lambda: datetime.now(),
                 writer: BudgetWriter | None = None):
        self.cache, self.logbuf, self.slot_dir = cache, logbuf, slot_dir
        self.is_pid_alive, self.clock = is_pid_alive, clock
        self.writer = writer or BudgetWriter(env={})   # ohne Angabe: Ändern aus

    def set_budget(self, alias: object, value: object) -> float:
        new = self.writer.set_budget(alias, value)
        self.cache.invalidate()   # die Anzeige holt den neuen Stand sofort
        return new

    def state(self) -> dict:
        status, veraltet = self.cache.get()
        slots = qwen_webhook.read_slots(self.slot_dir, self.is_pid_alive)
        state = build_state(slots, status, veraltet, self.logbuf.stephan(), self.clock())
        state["schreiben"] = self.writer.enabled   # zeigt den „Limit ändern“-Knopf
        stats = self.logbuf.stats
        state["statistik"] = stats.snapshot(self.clock()) if stats is not None else None
        return state

    def frames(self, last_seq: int, last_state_json: str | None):
        """Neue SSE-Rahmen seit dem letzten Stand: (Rahmen, last_seq, last_state_json)."""
        out: list[str] = []
        state = self.state()
        # `zeit` ändert sich jede Sekunde und löst allein kein Ereignis aus; die Laufzeit schon.
        cmp = json.dumps({k: v for k, v in state.items() if k != "zeit"}, ensure_ascii=False,
                         sort_keys=True)
        if cmp != last_state_json:
            out.append(sse("state", state))
            last_state_json = cmp
        new = self.logbuf.since(last_seq)
        if new:
            out.append(sse("log", [ln for _, ln in new]))
            last_seq = new[-1][0]
        return out, last_seq, last_state_json


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


PAGE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agenten-Sandbox</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2128;--mut:#6b7480;--line:#dde1e6;--blue:#1f6feb;
--grn:#1a7f37;--red:#cf222e;--yel:#9a6700;--gry:#8c959f}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--mut:#8b949e;
--line:#30363d;--blue:#58a6ff;--grn:#3fb950;--red:#f85149;--yel:#d29922;--gry:#8b949e}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,sans-serif;padding:16px}
h1{font-size:18px;margin:0 0 12px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.04em;
color:var(--mut);margin:20px 0 8px}
.kopf{display:flex;flex-wrap:wrap;gap:8px}.kpi{background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:8px 12px;min-width:110px}.kpi b{display:block;font-size:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;color:#fff;background:var(--gry)}
.running{background:var(--blue)}.idle{background:var(--gry)}.tot{background:var(--red)}
.mut{color:var(--mut)}.banner{background:var(--red);color:#fff;padding:8px 12px;border-radius:8px;margin:8px 0}
.warn{background:var(--yel);color:#fff;padding:6px 12px;border-radius:8px;margin:8px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
td,th{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}
.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin-top:4px}
.bar i{display:block;height:100%;background:var(--grn)}.bar i.hi{background:var(--red)}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px;height:320px;
overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;margin:0}
.st{color:var(--red);font-weight:600}a{color:var(--blue)}
.table-wrap{overflow-x:auto}
button{font:inherit;font-size:12px;padding:1px 8px;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg);cursor:pointer}
</style></head><body>
<h1>Agenten-Sandbox <span id="conn" class="mut"></span></h1>
<div id="alerts"></div><div id="veraltet"></div>
<div class="kopf" id="kopf"></div>
<h2>Worker-Slots</h2><div class="grid" id="slots"></div>
<h2>Braucht Stephan</h2><div id="stephan"></div>
<h2>Projekte</h2><div class="table-wrap"><table id="projekte"></table></div>
<h2>Reviews und Merges</h2><div class="table-wrap"><table id="statistik"></table></div>
<h2>LiteLLM-Budgets</h2><div class="grid" id="budgets"></div>
<h2>Log <label class="mut"><input type="checkbox" id="nur"> nur ⚠ Stephan</label></h2>
<pre id="log"></pre>
<script>
const $=id=>document.getElementById(id);
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e};
const fmt=s=>s==null?"":(s>=3600?Math.floor(s/3600)+" h ":"")+Math.floor(s%3600/60)+" min";
let logLines=[];
function renderStat(st){const t=$("statistik");t.replaceChildren();if(!st)return;
 const h=el("tr");["","Freigaben","Änderungen nötig","Quote","Gemergt","Merge-Fehler","An Stephan","Worker-Fehler"].forEach(x=>h.append(el("th",null,x)));t.append(h);
 const row=(name,c)=>{const r=el("tr");r.append(el("td",null,name));
  [c.freigegeben,c.aenderungen,c.quote==null?"–":c.quote+" %",c.gemergt,c.merge_fehler,c.an_stephan,c.worker_fehler].forEach(x=>r.append(el("td",null,x)));t.append(r)};
 row("24 Stunden",st["24h"]);row("7 Tage",st["7d"]);
 Object.entries(st.projekte).forEach(([n,c])=>row("7 Tage · "+n,c))}
async function setBudget(alias,cur){
 const v=prompt("Neues Limit für "+alias+" in USD (jetzt "+cur+"):",cur);if(v===null)return;
 const num=Number(v.replace(",","."));if(!isFinite(num)||v.trim()===""){alert("Keine Zahl");return}
 let tok="";try{tok=sessionStorage.getItem("wt")||""}catch(e){}
 if(!tok){tok=prompt("Schreib-Token:")||"";if(!tok)return}
 const r=await fetch("api/budget",{method:"POST",headers:{"Content-Type":"application/json","X-Dashboard-Token":tok},
  body:JSON.stringify({alias:alias,max_budget:num})});
 const j=await r.json().catch(()=>({}));
 if(r.ok){try{sessionStorage.setItem("wt",tok)}catch(e){}alert("Limit von "+alias+" auf $"+j.max_budget+" gesetzt")}
 else{if(r.status===403){try{sessionStorage.removeItem("wt")}catch(e){}}alert(j.fehler||"Fehler "+r.status)}
}
function renderLog(){const l=$("log"),nur=$("nur").checked,
 stick=l.scrollTop+l.clientHeight>=l.scrollHeight-20;
 l.textContent=logLines.filter(x=>!nur||x.includes("⚠ Stephan")).join("\n");
 if(stick)l.scrollTop=l.scrollHeight}
$("nur").onchange=renderLog;
function render(s){
 const al=$("alerts");al.replaceChildren(...s.alerts.map(a=>el("div","banner",a.text)));
 $("veraltet").replaceChildren(...(s.veraltet?[el("div","warn","GitHub-/Budgetdaten veraltet oder nicht erreichbar")]:[]));
 $("kopf").replaceChildren(...[["Aktive Slots",s.kopf.aktive_slots],["Offene Issues",s.kopf.offene_issues],
  ["Offene PRs",s.kopf.offene_prs],["Stand",(s.stand||"–").slice(11,19)]].map(([k,v])=>{
  const d=el("div","kpi");d.append(el("span","mut",k),el("b",null,v));return d}));
 $("slots").replaceChildren(...s.slots.map(v=>{const d=el("div","card");
  d.append(el("span","badge "+(v.tot?"tot":v.status),v.tot?"tot":v.status),el("b",null," Slot "+v.slot));
  const rows=[v.project,v.model,v.branch,v.message];
  if(v.issue)rows.push("Issue #"+v.issue);if(v.pr)rows.push("PR #"+v.pr);
  if(v.laufzeit_s!=null)rows.push("Laufzeit "+fmt(v.laufzeit_s));
  rows.filter(Boolean).forEach(r=>d.append(el("div","mut",r)));return d}));
 const st=s.projekte.flatMap(p=>(p.prs?p.prs.wartet_auf_stephan:[]).map(x=>({...x,projekt:p.name})));
 $("stephan").replaceChildren(...(st.length?st.map(x=>{const d=el("div","card");
  const a=el("a",null,"#"+x.number+" "+x.title);a.href=x.url;a.rel="noopener";
  d.append(el("span","mut",x.projekt+" · "),a);return d}):[el("span","mut","Nichts offen")]));
 const t=$("projekte");t.replaceChildren();
 const h=el("tr");["Projekt","bereit","in Arbeit","blockiert","Entwurf","PRs","rot","laufend"].forEach(x=>h.append(el("th",null,x)));t.append(h);
 s.projekte.forEach(p=>{const r=el("tr");r.append(el("td",null,p.name));
  if(p.fehler){const c=el("td",null,"Fehler: "+p.fehler);c.colSpan=7;r.append(c)}
  else[p.issues.bereit,p.issues["in-arbeit"],p.issues.blockiert,p.issues.entwurf,p.prs.offen,p.prs.rot,p.prs.laufend].forEach(x=>r.append(el("td",null,x)));
  t.append(r)});
 renderStat(s.statistik);
 $("budgets").replaceChildren(...Object.entries(s.budgets).map(([k,b])=>{const d=el("div","card");
  d.append(el("b",null,k));
  if(s.schreiben&&b.max!=null){const bt=el("button",null,"Limit ändern");bt.onclick=()=>setBudget(k,b.max);d.append(" ",bt)}
  if(b.fehler)d.append(el("div","mut",b.fehler));else{
   d.append(el("div","mut","$"+b.spend+(b.max?" / $"+b.max:"")+(b.prozent!=null?" ("+b.prozent+" %)":"")));
   const bar=el("div","bar"),i=el("i",b.prozent>=90?"hi":"");i.style.width=Math.min(b.prozent||0,100)+"%";bar.append(i);d.append(bar)}
  return d}))}
const es=new EventSource("events");
es.addEventListener("state",e=>render(JSON.parse(e.data)));
es.addEventListener("log",e=>{logLines=logLines.concat(JSON.parse(e.data)).slice(-400);renderLog()});
es.onopen=()=>$("conn").textContent="";
es.onerror=()=>$("conn").textContent="(Verbindung unterbrochen)";
</script></body></html>
"""


def make_handler(dash: Dashboard, interval: float = SSE_INTERVAL):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):   # keine Zugriffszeilen ins Journal
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):   # noqa: N802 — Name vorgegeben von BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/healthz":
                self._send(200, b"ok", "text/plain")
            elif path == "/api/state":
                body = json.dumps(dash.state(), ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
            elif path == "/events":
                self._events()
            else:
                self._send(404, b"nicht gefunden", "text/plain; charset=utf-8")

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_POST(self):   # noqa: N802
            if self.path.split("?", 1)[0] != "/api/budget":
                self._send(404, b"nicht gefunden", "text/plain; charset=utf-8")
                return
            writer = dash.writer
            # Der Header X-Dashboard-Token löst bei fremden Seiten einen Preflight aus, den dieser
            # Dienst nie beantwortet (kein CORS): Fremde Webseiten können nichts absenden.
            if not writer.enabled:
                self._json(403, {"fehler": "Budget ändern ist nicht eingerichtet"})
                return
            if not writer.token_ok(self.headers.get("X-Dashboard-Token", "")):
                time.sleep(writer.fail_delay)
                print("dashboard: Budget ändern mit falschem Token abgelehnt", file=sys.stderr, flush=True)
                self._json(403, {"fehler": "Token falsch"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"fehler": "Ungültige Anfrage"})
                return
            try:
                value = dash.set_budget(data.get("alias"), data.get("max_budget"))
            except BudgetError as exc:
                print(f"dashboard: Budget ändern fehlgeschlagen: {exc}", file=sys.stderr, flush=True)
                self._json(exc.status, {"fehler": str(exc)})
                return
            print(f"dashboard: Budget-Limit {data['alias']} auf {value} gesetzt", flush=True)
            self._json(200, {"ok": True, "alias": data["alias"], "max_budget": value})

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            # Eine neue Verbindung bekommt die vorgehaltenen Log-Zeilen und den Stand sofort.
            last_seq, last_state = 0, None
            try:
                while True:
                    dash.logbuf.poll()
                    frames, last_seq, last_state = dash.frames(last_seq, last_state)
                    for frame in frames:
                        self.wfile.write(frame.encode("utf-8"))
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    time.sleep(interval)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return   # Browser hat die Verbindung geschlossen

    return Handler


def serve(bind: str = BIND, port: int = PORT) -> ThreadingHTTPServer:
    stats = dashboard_stats.ReviewStats()
    dash = Dashboard(StatusCache(default_builder), LogBuffer(stats=stats), writer=BudgetWriter())
    server = ThreadingHTTPServer((bind, port), make_handler(dash))
    server.daemon_threads = True
    notifier = dashboard_notify.Notifier()
    server.push = notifier.enabled
    if notifier.enabled:
        loop = dashboard_notify.NotifyLoop(dash, notifier)
        threading.Thread(target=loop.run, args=(NOTIFY_INTERVAL,), daemon=True).start()
    return server


def main() -> None:
    if "--notify-test" in sys.argv[1:]:
        sys.exit(dashboard_notify.send_test(dashboard_notify.Notifier()))
    server = serve()
    print(f"Dashboard läuft auf {BIND}:{server.server_address[1]}, Push {'an' if server.push else 'aus'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
