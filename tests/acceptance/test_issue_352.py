import importlib.util
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
_spec = importlib.util.spec_from_file_location("dashboard_token", _SANDBOX / "dashboard.py")
d = importlib.util.module_from_spec(_spec)
sys.modules["dashboard_token"] = d
_spec.loader.exec_module(d)

NOW = datetime(2026, 10, 24, 12, 0, 0)


def test_cache_rechnet_hoechstens_alle_ttl_sekunden(tmp_path):
    uhr = [100.0]
    aufrufe = []

    def builder(path, now):
        aufrufe.append((path, now))
        return {"7d": {"laeufe": len(aufrufe)}}

    pfad = str(tmp_path / "audit.jsonl")
    cache = d.TokenStatsCache(pfad, ttl=60, clock=lambda: uhr[0], builder=builder)
    assert cache.get(NOW)["7d"]["laeufe"] == 1
    uhr[0] = 159.0
    assert cache.get(NOW)["7d"]["laeufe"] == 1
    uhr[0] = 160.0
    assert cache.get(NOW)["7d"]["laeufe"] == 2
    assert aufrufe[0] == (pfad, NOW)


def test_cache_wirft_nie_und_versucht_nach_ttl_erneut(tmp_path):
    uhr = [0.0]
    antworten = [RuntimeError("kaputt"), {"7d": {"laeufe": 1}}]

    def builder(path, now):
        a = antworten.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    cache = d.TokenStatsCache(str(tmp_path / "a.jsonl"), ttl=60, clock=lambda: uhr[0], builder=builder)
    assert cache.get(NOW) is None
    uhr[0] = 30.0
    assert cache.get(NOW) is None
    uhr[0] = 61.0
    assert cache.get(NOW) == {"7d": {"laeufe": 1}}


def test_vorgabe_liest_das_audit_log(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    zeile = {"ts": NOW.astimezone().isoformat(), "event": "agent_run_finished", "model": "coder-go",
             "tokens": {"input_tokens": 1000, "output_tokens": 10, "cache_read_input_tokens": 400}}
    log.write_text(json.dumps(zeile) + "\n", encoding="utf-8")
    monkeypatch.setattr(d, "AUDIT_LOG", str(log))
    out = d.TokenStatsCache(ttl=60).get(NOW)
    assert out["7d"]["ein"] == 1000 and out["7d"]["cache_quote"] == 40
    assert out["24h"]["modelle"]["coder-go"]["laeufe"] == 1


def test_state_enthaelt_token(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_CONF", str(tmp_path / "agent-sandbox.conf"))
    log = tmp_path / "worker.log"
    log.touch()

    def dash(**kw):
        return d.Dashboard(d.StatusCache(lambda: None, ttl=60), d.LogBuffer(str(log)),
                           slot_dir=str(tmp_path / "slots"), infra_dir=str(tmp_path),
                           is_pid_alive=lambda pid: True, clock=lambda: NOW, **kw)

    cache = d.TokenStatsCache(str(tmp_path / "a.jsonl"), builder=lambda p, now: {"7d": {"laeufe": 3}})
    assert dash(token_stats=cache).state()["token"] == {"7d": {"laeufe": 3}}
    assert dash().state()["token"] is None


def test_seite_zeigt_die_token_tabelle_und_serve_verdrahtet_den_cache():
    assert "<h2>Token je Agentenlauf</h2>" in d.PAGE
    assert 'id="token"' in d.PAGE
    assert "renderToken(s.token);" in d.PAGE
    assert d.PAGE.index("renderStat(s.statistik);") < d.PAGE.index("renderToken(s.token);")
    assert "TokenStatsCache(" in inspect.getsource(d.serve)
