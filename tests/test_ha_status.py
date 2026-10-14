"""Tests für sandbox/ha_status.py (Status-JSON für den HA-Webhook), ohne Netzwerk und ohne gh.

Das Skript liegt neben projects.py und wird wie die anderen Module per sys.path geladen.
Alle äußeren Aufrufe (`gh`, LiteLLM, Webhook) sind hier gefakt; `main()` wird nur über
seine Parameter gesteuert.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))

import ha_status  # noqa: E402
import projects  # noqa: E402

WEBHOOK = "http://ha.example:8123/api/webhook/GEHEIM123"
WORKER_KEY = "sk-worker-GEHEIM456"
REVIEW_KEY = "sk-review-GEHEIM789"
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)


def _project(name="a", repo="o/a"):
    return projects.Project(name=name, repo=repo, workdir=f"/root/{name}")


def _env(**extra):
    env = {"HA_STATUS_URL": WEBHOOK, "LITELLM_WORKER_KEY": WORKER_KEY, "LITELLM_REVIEW_KEY": REVIEW_KEY}
    env.update(extra)
    return env


def _fetch(kind, repo):
    if kind == "issues":
        return [{"number": 1, "title": "I1", "labels": [{"name": "bereit"}]}]
    return [{"number": 2, "title": "P2", "url": "u", "isDraft": False, "labels": [], "statusCheckRollup": []}]


def _key_info(key):
    return {"spend": 1.0, "max_budget": 4.0}


class _Resp:
    def __init__(self, status=204):
        self.status = status

    def close(self):
        pass


# ---------------- main() ----------------
def test_print_emits_valid_json_and_sends_nothing(capsys, monkeypatch):
    monkeypatch.setattr(projects, "load_projects", lambda **kw: [_project()])
    sent = []

    code = ha_status.main(["--print"], _env(), fetch=_fetch, key_info=_key_info,
                          opener=lambda *a, **k: sent.append(a), now=NOW)
    assert code == 0
    assert sent == []
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["zeit"] == "2026-10-14T16:00:00+00:00"
    assert [p["name"] for p in data["projekte"]] == ["a"]
    assert data["projekte"][0]["prs"]["offen"] == 1
    assert data["budgets"]["worker"] == {"spend": 1.0, "max": 4.0, "prozent": 25}
    assert WEBHOOK not in out and WORKER_KEY not in out and REVIEW_KEY not in out


def test_missing_url_returns_2(capsys, monkeypatch):
    monkeypatch.setattr(projects, "load_projects", lambda **kw: [])
    code = ha_status.main([], {}, fetch=_fetch, key_info=_key_info, now=NOW)
    assert code == 2
    assert "HA_STATUS_URL fehlt" in capsys.readouterr().err


def test_failed_send_returns_1_and_prints_no_secret(capsys, monkeypatch):
    monkeypatch.setattr(projects, "load_projects", lambda **kw: [_project()])

    def boom(req, timeout=None):
        raise OSError(f"kein Weg zu {WEBHOOK}")

    code = ha_status.main([], _env(), fetch=_fetch, key_info=_key_info, opener=boom, now=NOW)
    assert code == 1
    captured = capsys.readouterr()
    assert "Senden fehlgeschlagen" in captured.err
    assert "GEHEIM123" not in captured.out + captured.err
    assert WORKER_KEY not in captured.out + captured.err


def test_successful_send_posts_to_webhook_and_returns_0(monkeypatch):
    monkeypatch.setattr(projects, "load_projects", lambda **kw: [_project()])
    seen = []

    def opener(req, timeout=None):
        seen.append(req)
        return _Resp(200)

    code = ha_status.main([], _env(), fetch=_fetch, key_info=_key_info, opener=opener, now=NOW)
    assert code == 0
    assert len(seen) == 1 and seen[0].full_url == WEBHOOK
    assert json.loads(seen[0].data.decode("utf-8"))["braucht_stephan"] == 0


def test_unset_keys_are_omitted_from_budgets(capsys, monkeypatch):
    monkeypatch.setattr(projects, "load_projects", lambda **kw: [])
    env = {"HA_STATUS_URL": WEBHOOK, "LITELLM_REVIEW_KEY": REVIEW_KEY}
    code = ha_status.main(["--print"], env, fetch=_fetch, key_info=_key_info, now=NOW)
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["budgets"]) == {"reviewer"}


def test_main_asks_key_info_once_per_set_key(capsys, monkeypatch):
    monkeypatch.setattr(projects, "load_projects", lambda **kw: [])
    asked = []

    def key_info(key):
        asked.append(key)
        return None

    code = ha_status.main(["--print"], _env(), fetch=_fetch, key_info=key_info, now=NOW)
    assert code == 0
    assert asked == [WORKER_KEY, REVIEW_KEY]
    data = json.loads(capsys.readouterr().out)
    assert "planer" not in data["budgets"]


# ---------------- gh ----------------
def test_gh_fetch_builds_issue_and_pr_lists(monkeypatch):
    calls = []

    class Out:
        def __init__(self, text):
            self.stdout = text

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Out("[]")

    monkeypatch.setattr(ha_status.subprocess, "run", fake_run)
    assert ha_status._gh_fetch("issues", "o/a") == []
    assert ha_status._gh_fetch("prs", "o/b") == []
    assert calls[0][:3] == ["gh", "issue", "list"]
    assert "o/a" in calls[0] and "worker-task" in calls[0]
    assert any("labels" in part for part in calls[0])
    assert calls[1][:3] == ["gh", "pr", "list"] and "statusCheckRollup" in " ".join(calls[1])


def test_gh_fetch_parses_json(monkeypatch):
    monkeypatch.setattr(ha_status.subprocess, "run",
                        lambda cmd, **kw: type("O", (), {"stdout": '[{"number": 3}]'})())
    assert ha_status._gh_fetch("issues", "o/a") == [{"number": 3}]


# ---------------- LiteLLM ----------------
def test_default_key_info_reads_info_field(monkeypatch):
    seen = {}

    class Resp:
        def read(self):
            return b'{"info": {"spend": 2.0, "max_budget": 8.0}}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        seen["req"] = req
        return Resp()

    monkeypatch.setattr(ha_status.urllib.request, "urlopen", fake_urlopen)
    info = ha_status._default_key_info(WORKER_KEY, "http://litellm.example:4000/")
    assert info == {"spend": 2.0, "max_budget": 8.0}
    assert seen["req"].full_url == "http://litellm.example:4000/key/info"
    assert seen["req"].get_header("Authorization") == f"Bearer {WORKER_KEY}"


def test_default_key_info_returns_none_on_error(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("weg")

    monkeypatch.setattr(ha_status.urllib.request, "urlopen", boom)
    assert ha_status._default_key_info(WORKER_KEY, "http://litellm.example:4000") is None


# ---------------- Bausteine ----------------
def test_budget_zero_max_has_no_percentage():
    assert ha_status.summarize_budget({"spend": 3.0, "max_budget": 0})["prozent"] is None


def test_post_status_without_numeric_status_is_false():
    assert ha_status.post_status(WEBHOOK, {}, opener=lambda req, timeout=None: _Resp(None)) is False
