"""Akzeptanztests für Issue #<N>: Pflicht-Checks kommen aus der Projektliste, nicht aus dem Code.

Heute steht `REQUIRED_CHECKS = ("tests", "acceptance-guard", "CodeQL")` fest im Review-Takt
(review_tick.py:71). Das ist sc-diggers Vertrag; dieses Repo nennt nur `tests` und
`acceptance-guard` und hält einen roten `github-advanced-security` für keinen Mangel. Zufällig
ist das harmlos, weil `checks_summary` nur vorhandene Checks bewertet — sobald hier jemand CodeQL
einschaltet und ein Lauf `NEUTRAL` endet, entsteht ohne Modellaufruf „Änderungen nötig".
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


p = _load("projects", "sandbox/projects.py")
r = _load("review_tick", "sandbox/review_tick.py")

OHNE = {"projects": [{"name": "a", "repo": "o/a", "workdir": "/root/a"}]}
MIT = {"projects": [{"name": "a", "repo": "o/a", "workdir": "/root/a",
                     "required_checks": ["tests", "acceptance-guard", "CodeQL"]}]}


def test_vorgabe_folgt_dem_vertrag_dieses_repos():
    projekt = p.parse_projects(json.dumps(OHNE))[0]
    assert tuple(projekt.required_checks) == ("tests", "acceptance-guard")


def test_eigene_liste_wird_gelesen():
    projekt = p.parse_projects(json.dumps(MIT))[0]
    assert tuple(projekt.required_checks) == ("tests", "acceptance-guard", "CodeQL")


def test_use_project_setzt_die_pflicht_checks():
    projekt = p.parse_projects(json.dumps(MIT))[0]
    r.use_project(projekt, first=True)
    assert tuple(r.REQUIRED_CHECKS) == ("tests", "acceptance-guard", "CodeQL")


def test_nicht_vorhandener_pflicht_check_bleibt_folgenlos():
    # Unverändertes Verhalten: `checks_summary` bewertet nur vorhandene Checks.
    done, rows, red = r.checks_summary(
        [{"name": "tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
         {"name": "CodeQL", "status": "COMPLETED", "conclusion": "NEUTRAL"}])
    assert done is True and red == []