import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))
_spec = importlib.util.spec_from_file_location("dashboard_einstellungen", _SANDBOX / "dashboard.py")
d = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_einstellungen"] = d
_spec.loader.exec_module(d)

NOW = datetime(2026, 10, 18, 12, 0, 0)
STATUS = {"zeit": "2026-10-18T10:00:00+00:00", "projekte": [], "braucht_stephan": 0, "budgets": {}}
TOKEN = "ein-langer-schreib-token"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "LOG", str(tmp_path / "worker.log"))
    monkeypatch.setattr(d, "SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("AGENT_SETTINGS", str(tmp_path / "agent-settings.json"))
    for var in ("REVIEW_MAX_TOKENS", "REVIEW_LLM_TIMEOUT", "REVIEW_MODEL_BIG"):
        monkeypatch.delenv(var, raising=False)


def _dash(tmp_path, token=TOKEN):
    log = tmp_path / "worker.log"
    log.touch()
    dash = d.Dashboard(d.StatusCache(lambda: STATUS, ttl=60), d.LogBuffer(str(log)),
                       slot_dir=str(tmp_path / "slots"), is_pid_alive=lambda pid: True, clock=lambda: NOW)
    dash.settings_writer = d.SettingsWriter(env={"DASHBOARD_WRITE_TOKEN": token} if token else {})
    dash.settings_writer.fail_delay = 0
    return dash


def _datei(tmp_path) -> Path:
    return tmp_path / "agent-settings.json"


def _post(base, payload, token=TOKEN, raw=None, path="/api/settings"):
    data = raw if raw is not None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Dashboard-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


@pytest.fixture
def served(tmp_path):
    dash = _dash(tmp_path)
    server = d.ThreadingHTTPServer(("127.0.0.1", 0), d.make_handler(dash, interval=0.01))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", dash
    server.shutdown()
    server.server_close()


def test_state_zeigt_werte_quellen_und_spezifikation(tmp_path):
    _datei(tmp_path).write_text(json.dumps({"MAX_ROUNDS": 3}), encoding="utf-8")
    e = _dash(tmp_path).state()["einstellungen"]
    assert e["werte"]["MAX_ROUNDS"] == {"wert": 3, "quelle": "datei"}
    assert e["werte"]["MAX_TRIES"]["quelle"] == "vorgabe"
    assert sorted(e["werte"]) == sorted(e["spezifikation"])
    spec = e["spezifikation"]["REVIEW_MAX_TOKENS"]
    assert spec["min"] == 1000 and spec["max"] == 100000 and spec["text"]
    assert e["spezifikation"]["REVIEW_MODEL_BIG"]["erlaubt"] == ["coder-glm", "longctx-lite"]
    json.dumps(e)                                              # alles JSON-fähig, keine Python-Typen darin


def test_schreiben_braucht_nur_den_token_nicht_den_master_key(tmp_path):
    assert _dash(tmp_path).state()["einstellungen"]["schreiben"] is True
    assert _dash(tmp_path, token="").state()["einstellungen"]["schreiben"] is False
    assert _dash(tmp_path, token="zu-kurz").state()["einstellungen"]["schreiben"] is False


def test_post_setzt_den_wert_und_schreibt_die_datei(served, tmp_path):
    base, dash = served
    status, body = _post(base, {"name": "REVIEW_MAX_TOKENS", "value": 32000})
    assert status == 200 and body == {"ok": True, "name": "REVIEW_MAX_TOKENS", "value": 32000}
    assert json.loads(_datei(tmp_path).read_text(encoding="utf-8")) == {"REVIEW_MAX_TOKENS": 32000}
    assert dash.state()["einstellungen"]["werte"]["REVIEW_MAX_TOKENS"] == {"wert": 32000, "quelle": "datei"}
    assert _post(base, {"name": "MAX_ROUNDS", "value": 3})[0] == 200          # ergänzt, überschreibt nichts
    assert json.loads(_datei(tmp_path).read_text(encoding="utf-8")) == {"REVIEW_MAX_TOKENS": 32000, "MAX_ROUNDS": 3}


def test_ohne_oder_mit_falschem_token_aendert_sich_nichts(served, tmp_path):
    base, _ = served
    for token in (None, "", "falsch", TOKEN[:-1]):
        status, body = _post(base, {"name": "MAX_ROUNDS", "value": 3}, token=token)
        assert status == 403 and body == {"fehler": "Token falsch"}
    assert not _datei(tmp_path).exists()


def test_ohne_einrichtung_ist_das_aendern_gesperrt(served, tmp_path):
    base, dash = served
    dash.settings_writer = d.SettingsWriter(env={})
    status, body = _post(base, {"name": "MAX_ROUNDS", "value": 3})
    assert status == 403 and "nicht eingerichtet" in body["fehler"]
    assert not _datei(tmp_path).exists()


def test_ungueltige_eingaben_werden_abgelehnt_und_aendern_nichts(served, tmp_path):
    base, _ = served
    assert _post(base, None, raw=b"kein json")[0] == 400
    assert _post(base, None, raw=b"[1]")[0] == 400
    assert _post(base, None, raw=b"")[0] == 400
    assert _post(base, None, raw=b"x" * 2000)[0] == 400
    for payload in ({"name": "AUTO_MERGE", "value": 1}, {"name": "REVIEW_MODEL", "value": "coder-glm"},
                    {"name": "MAX_ROUNDS", "value": 99}, {"name": "MAX_ROUNDS", "value": "3"},
                    {"name": "REVIEW_MAX_TOKENS", "value": True}, {"name": "REVIEW_MODEL_BIG", "value": "coder-fast"},
                    {"name": ["MAX_ROUNDS"], "value": 3}, {"value": 3}):
        status, body = _post(base, payload)
        assert status == 400 and body["fehler"], payload
    assert not _datei(tmp_path).exists()


def test_post_auf_einen_anderen_pfad_ist_weiter_404(served):
    req = urllib.request.Request(served[0] + "/api/nix", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 404


def test_das_budget_bleibt_unveraendert_erreichbar(served):
    # Der alte Pfad antwortet weiter (hier ohne Master-Key: „nicht eingerichtet“), er wurde nicht umgeleitet.
    status, body = _post(served[0], {"alias": "worker", "max_budget": 5}, path="/api/budget")
    assert status == 403 and "nicht eingerichtet" in body["fehler"]


def test_journal_nennt_aenderung_und_ablehnung_ohne_token(served, capsys):
    base, _ = served
    _post(base, {"name": "MAX_ROUNDS", "value": 3})
    _post(base, {"name": "MAX_ROUNDS", "value": 3}, token="falsch-aber-lang-genug")
    ausgabe = capsys.readouterr()
    assert "dashboard: Einstellung MAX_ROUNDS auf 3 gesetzt" in ausgabe.out
    assert "Einstellungen ändern mit falschem Token abgelehnt" in ausgabe.err
    assert TOKEN not in ausgabe.out + ausgabe.err and "falsch-aber-lang-genug" not in ausgabe.out + ausgabe.err


def test_antworten_und_state_enthalten_nie_den_token(served):
    base, dash = served
    texte = [json.dumps(dash.state()), json.dumps(_post(base, {"name": "MAX_ROUNDS", "value": 3})[1]),
             json.dumps(_post(base, {"name": "MAX_ROUNDS", "value": 99})[1]),
             urllib.request.urlopen(base + "/", timeout=5).read().decode()]
    assert all(TOKEN not in t for t in texte)


def test_die_seite_hat_den_abschnitt_einstellungen(served):
    html = urllib.request.urlopen(served[0] + "/", timeout=5).read().decode()
    assert "Einstellungen" in html and "api/settings" in html
