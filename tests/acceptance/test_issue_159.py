import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard.py"
_spec = importlib.util.spec_from_file_location("dashboard_issue_wake", _PATH)
d = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_issue_wake"] = d
_spec.loader.exec_module(d)

TOKEN = "t" * 32
URL = "https://ntfy.test/wake-geheimes-thema"
ENV = {"DASHBOARD_WRITE_TOKEN": TOKEN, "WAKE_PUBLISH_URL": URL}
NOW = datetime(2026, 10, 18, 12, 0, 0)
STATUS = {"zeit": "2026-10-18T10:00:00+00:00", "projekte": [], "braucht_stephan": 0, "budgets": {}}


def _dash(tmp_path, waker=None):
    log = tmp_path / "worker.log"
    log.touch()
    kwargs = {} if waker is None else {"waker": waker}
    return d.Dashboard(d.StatusCache(lambda: STATUS, ttl=60), d.LogBuffer(str(log)),
                       slot_dir=str(tmp_path / "slots"), is_pid_alive=lambda pid: True,
                       clock=lambda: NOW, **kwargs)


def test_sender_ist_ohne_url_oder_kurzen_token_aus():
    assert d.WakeSender(env={}).enabled is False
    assert d.WakeSender(env={"DASHBOARD_WRITE_TOKEN": TOKEN}).enabled is False
    assert d.WakeSender(env={"WAKE_PUBLISH_URL": URL}).enabled is False
    assert d.WakeSender(env={"DASHBOARD_WRITE_TOKEN": "kurz", "WAKE_PUBLISH_URL": URL}).enabled is False
    assert d.WakeSender(env={"DASHBOARD_WRITE_TOKEN": TOKEN, "WAKE_PUBLISH_URL": "ftp://x/y"}).enabled is False
    assert d.WakeSender(env={"DASHBOARD_WRITE_TOKEN": TOKEN, "WAKE_PUBLISH_URL": "thema"}).enabled is False
    assert d.WakeSender(env=ENV).enabled is True


def test_token_ok_vergleicht_den_schreib_token():
    s = d.WakeSender(env=ENV)
    assert s.token_ok(TOKEN) is True
    for falsch in ("", "falsch", TOKEN[:-1], TOKEN + "x"):
        assert s.token_ok(falsch) is False
    assert d.WakeSender(env={}).token_ok("") is False


def test_send_ruft_publish_mit_der_url_auf():
    sent = []
    d.WakeSender(env=ENV, publish=sent.append, clock=lambda: 100.0).send()
    assert sent == [URL]


def test_send_ist_gedrosselt():
    sent = []
    now = [100.0]
    s = d.WakeSender(env=ENV, publish=sent.append, clock=lambda: now[0])
    s.send()
    now[0] = 105.0
    with pytest.raises(d.WakeError) as err:
        s.send()
    assert err.value.status == 429
    assert sent == [URL]
    now[0] = 111.0
    s.send()
    assert sent == [URL, URL]


def test_standard_publish_sendet_das_wort_wake_per_post(monkeypatch):
    seen = []

    class Resp:
        def close(self):
            pass

    def fake(req, timeout):
        seen.append(req)
        return Resp()

    monkeypatch.setattr(d.urllib.request, "urlopen", fake)
    d.WakeSender(env=ENV).send()
    assert len(seen) == 1
    assert seen[0].full_url == URL and seen[0].data == b"wake" and seen[0].get_method() == "POST"


def test_fehlertexte_nennen_nie_die_url(monkeypatch):
    def netz(req, timeout):
        raise OSError(URL)

    monkeypatch.setattr(d.urllib.request, "urlopen", netz)
    with pytest.raises(d.WakeError) as err:
        d.WakeSender(env=ENV).send()
    assert str(err.value) == "Wecken nicht möglich (OSError)" and err.value.status == 502

    def abgelehnt(req, timeout):
        raise urllib.error.HTTPError(URL, 403, "verboten", {}, None)

    monkeypatch.setattr(d.urllib.request, "urlopen", abgelehnt)
    with pytest.raises(d.WakeError) as err:
        d.WakeSender(env=ENV).send()
    assert str(err.value) == "ntfy lehnt ab (HTTP 403)" and err.value.status == 502
    assert "ntfy.test" not in str(err.value)


def test_state_meldet_ob_wecken_moeglich_ist(tmp_path):
    assert _dash(tmp_path).state()["wecken"] is False
    assert _dash(tmp_path, waker=d.WakeSender(env=ENV, publish=lambda url: None)).state()["wecken"] is True


@pytest.fixture
def served(tmp_path):
    sent = []
    sender = d.WakeSender(env=ENV, publish=sent.append)
    sender.fail_delay = 0
    dash = _dash(tmp_path, waker=sender)
    server = d.ThreadingHTTPServer(("127.0.0.1", 0), d.make_handler(dash, interval=0.01))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", sent, dash, sender
    server.shutdown()
    server.server_close()


def _wake(base, token=TOKEN):
    headers = {"X-Dashboard-Token": token} if token is not None else {}
    req = urllib.request.Request(base + "/api/wake", data=b"", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_post_wake_mit_richtigem_token(served):
    base, sent, _, _ = served
    assert _wake(base) == (200, {"ok": True})
    assert sent == [URL]


def test_post_wake_ohne_oder_mit_falschem_token_sendet_nichts(served):
    base, sent, _, _ = served
    for token in (None, "", "falsch", TOKEN[:-1]):
        assert _wake(base, token=token) == (403, {"fehler": "Token falsch"})
    assert sent == []


def test_post_wake_ohne_einrichtung_ist_gesperrt(served):
    base, sent, dash, _ = served
    dash.waker = d.WakeSender(env={}, publish=sent.append)
    status, body = _wake(base)
    assert status == 403 and "nicht eingerichtet" in body["fehler"]
    assert sent == []


def test_post_wake_zweimal_schnell_hintereinander_wird_gebremst(served):
    base, sent, _, _ = served
    assert _wake(base)[0] == 200
    status, body = _wake(base)
    assert status == 429 and body["fehler"]
    assert sent == [URL]


def test_post_wake_meldet_fehler_ohne_url(served):
    base, _, dash, _ = served

    def kaputt(url):
        raise OSError(url)

    dash.waker = d.WakeSender(env=ENV, publish=kaputt)
    status, body = _wake(base)
    assert status == 502 and "ntfy.test" not in json.dumps(body)


def test_post_auf_anderen_pfad_bleibt_404(served):
    base, _, _, _ = served
    req = urllib.request.Request(base + "/api/nix", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 404


def test_seite_hat_den_knopf():
    assert "api/wake" in d.PAGE and "Takt starten" in d.PAGE