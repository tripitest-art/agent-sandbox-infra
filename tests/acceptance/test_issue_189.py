import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"
_spec = importlib.util.spec_from_file_location("worker_tick", _PATH)
w = importlib.util.module_from_spec(_spec)
sys.modules["worker_tick"] = w
_spec.loader.exec_module(w)


def test_exempt_enthaelt_changelog_und_readme():
    # #405: `Unreleased` (Backtick-Wort in einer CHANGELOG-Zeile, zählt wie `CHANGELOG.md` nicht)
    # kam zu der Menge der ausgenommenen Doku-Pflichtzeilen hinzu.
    assert w.COLLISION_EXEMPT == frozenset({"CHANGELOG.md", "README.md", "Unreleased"})


def test_doku_und_akzeptanztest_zaehlen_nicht():
    files = {"CHANGELOG.md", "README.md", "tests/acceptance/test_issue_350.py",
             "tests/acceptance/test_issue_", "sc_digger/bot.py"}
    assert w.collision_files(files) == {"sc_digger/bot.py"}


def test_code_und_andere_tests_zaehlen():
    files = {"sc_digger/main.py", "tests/test_worker.py", "docs/README.md", "tests/acceptance/helper.py"}
    assert w.collision_files(files) == files


def test_eingabe_bleibt_unveraendert():
    files = {"CHANGELOG.md", "sc_digger/bot.py"}
    w.collision_files(files)
    assert files == {"CHANGELOG.md", "sc_digger/bot.py"}


def test_leer():
    assert w.collision_files(set()) == set()
