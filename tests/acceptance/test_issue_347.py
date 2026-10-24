import re
from pathlib import Path

TEXT = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "wake.yml").read_text(encoding="utf-8")
CODE = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))


def _on_block():
    m = re.search(r"^on:\n(.*?)^\S", CODE + "\nENDE", re.M | re.S)
    assert m, "kein on:-Block"
    return m.group(1)


def test_abgegebenes_review_weckt_die_takte():
    block = _on_block()
    assert re.search(r"^  pull_request_review:\n    types: \[submitted\]\s*$", block, re.M)


def test_bisherige_ereignisse_bleiben():
    block = _on_block()
    assert re.search(r"^  issues:\n    types: \[labeled\]\s*$", block, re.M)
    assert re.search(r"^  pull_request:\n    types: \[opened, synchronize, reopened, labeled, closed\]\s*$",
                     block, re.M)
    assert re.search(r"^  workflow_run:\n    workflows: \[tests, acceptance-guard\]\n    types: \[completed\]\s*$",
                     block, re.M)


def test_sicherheit_unveraendert():
    assert "pull_request_target" not in CODE
    assert "checkout" not in CODE
    assert re.search(r"^permissions:\s*\{\}\s*$", CODE, re.M)
    assert '--data "wake"' in CODE
