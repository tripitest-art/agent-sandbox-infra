"""Statische Prüfung von .github/workflows/wake.yml (ohne YAML-Bibliothek, ohne Netzwerk).

Der Workflow läuft in einem öffentlichen Repository mit einem Geheimnis. Diese Tests halten die
Eigenschaften fest, an denen die Sicherheit hängt; sie führen den Workflow nicht aus.
"""

import re
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))   # ohne Kommentare


def test_kein_pull_request_target_und_kein_checkout():
    assert "pull_request_target" not in CODE
    assert "checkout" not in CODE


def test_keine_rechte_und_kein_ereignistext_im_skript():
    assert re.search(r"^permissions:\s*\{\}\s*$", CODE, re.M)
    run = CODE.split("run: |", 1)[1]
    assert "${{" not in run                  # nichts aus dem Ereignis in die Shell (Injection)
    assert 'WAKE_PUBLISH_URL: ${{ secrets.WAKE_PUBLISH_URL }}' in CODE


def test_nachricht_ist_fest_und_url_bleibt_geheim():
    assert '--data "wake"' in CODE
    assert not re.search(r"\bcurl\b[^\n]*\s(-v|-x|--verbose|--trace)\b", CODE)
    assert "set -x" not in CODE
    assert not re.search(r"https?://", CODE)   # kein Host oder Thema im Repo


def test_fehlendes_secret_und_fehlschlag_halten_den_lauf_gruen():
    assert 'if [ -z "$WAKE_PUBLISH_URL" ]' in CODE and "exit 0" in CODE
    assert "::warning::" in CODE


def test_ereignisse_passen_zu_den_takten():
    for needle in ("issues:", "pull_request:", "workflow_run:", "workflows: [tests, acceptance-guard]",
                   "'bereit'", "'review-anfordern'"):
        assert needle in CODE


def test_abgegebenes_review_weckt_ebenfalls_ohne_neue_rechte():
    # #347: Nach „Änderungen nötig“ wartet die Nacharbeit sonst bis zum nächsten Timer-Takt.
    assert re.search(r"^  pull_request_review:\n    types: \[submitted\]\s*$", CODE, re.M)
    assert "pull_request_target" not in CODE
    assert re.search(r"^permissions:\s*\{\}\s*$", CODE, re.M)
