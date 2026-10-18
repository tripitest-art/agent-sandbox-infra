import importlib.util
import sys
import urllib.error
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard_notify.py"
_spec = importlib.util.spec_from_file_location("dashboard_notify_issue_test", _PATH)
n = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_notify_issue_test"] = n
_spec.loader.exec_module(n)


def test_read_env_file(tmp_path):
    f = tmp_path / "e.env"
    f.write_text('# Kommentar\nNTFY_URL=https://ntfy.example/t\n\nNTFY_TOKEN="tk"\nLEER=\nKAPUTT\nA=b=c\n',
                 encoding="utf-8")
    assert n.read_env_file(str(f)) == {"NTFY_URL": "https://ntfy.example/t", "NTFY_TOKEN": "tk",
                                       "LEER": "", "A": "b=c"}


def test_read_env_file_fehlt(tmp_path):
    assert n.read_env_file(str(tmp_path / "gibt-es-nicht")) == {}


def test_notifier_for_test_datei_gewinnt(tmp_path):
    f = tmp_path / "e.env"
    f.write_text("NTFY_URL=https://ntfy.example/t\n", encoding="utf-8")
    note = n.notifier_for_test(str(f), {"NTFY_URL": "https://alt.example/x", "PATH": "/bin"})
    assert note.enabled and note.url == "https://ntfy.example/t"


def test_notifier_for_test_ohne_datei_gilt_die_umgebung(tmp_path):
    fehlt = str(tmp_path / "fehlt")
    assert n.notifier_for_test(fehlt, {"NTFY_URL": "https://alt.example/x"}).url == "https://alt.example/x"
    assert not n.notifier_for_test(fehlt, {}).enabled


def _opener(code, location):
    def opener(request, timeout):
        headers = {"Location": location} if location else {}
        raise urllib.error.HTTPError(request.full_url, code, "Umleitung", headers, None)
    return opener


def _send(code, location):
    note = n.Notifier(env={"NTFY_URL": "https://ntfy.example/geheimes-topic"}, opener=_opener(code, location))
    assert note.send(n.Event("k", "t", "m")) is False
    return note.last_error


def test_umleitung_nennt_nur_den_zielhost():
    err = _send(307, "https://ziel.example:8443/neu/geheimes-topic?x=1")
    assert err == "HTTP 307 → ziel.example:8443"
    assert "geheimes" not in err and "neu" not in err
    assert _send(301, "http://anderer.example/") == "HTTP 301 → anderer.example"


def test_umleitung_ohne_brauchbaren_location_bleibt_wie_bisher():
    assert _send(307, None) == "HTTP 307"
    assert _send(307, "/app") == "HTTP 307"
    assert _send(403, "https://ziel.example/x") == "HTTP 403"
