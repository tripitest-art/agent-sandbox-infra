"""Issue #423: `.claude/*` ist Aufsichtspfad, ein PR nur mit `.claude/...` gilt als „manuell“.

Geprüft wird die Beispiel-Registerdatei mit denselben Bausteinen wie der Takt:
`projects.parse_projects` und `review_tick.manual_path_hits`. Offline, ohne gh.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_tick_claude", ROOT / "sandbox" / "review_tick.py")
r = importlib.util.module_from_spec(_spec)
sys.modules["review_tick_claude"] = r
_spec.loader.exec_module(r)


def _infra_manual_paths() -> tuple[str, ...]:
    text = (ROOT / "sandbox" / "projects.example.json").read_text(encoding="utf-8")
    return next(p for p in r.projects.parse_projects(text) if p.name == "agent-sandbox-infra").manual_paths


def test_pr_der_nur_claude_aendert_gilt_als_manuell():
    files = [".claude/agents/reviewer.md", ".claude/settings.json"]
    assert r.manual_path_hits(files, _infra_manual_paths()) == files


def test_claude_eintrag_trifft_nur_das_claude_verzeichnis():
    hits = r.manual_path_hits(["docs/claude.md", "sandbox/x.py", ".claudeignore"], _infra_manual_paths())
    assert hits == []


def test_bisherige_aufsichtspfade_bleiben_erhalten():
    for path in ("AGENTS.md", ".github/workflows/x.yml", "tests/acceptance/test_issue_1.py", "skills/a/SKILL.md"):
        assert r.manual_path_hits([path], _infra_manual_paths()) == [path]
