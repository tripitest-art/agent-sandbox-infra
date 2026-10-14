import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))

import projects  # noqa: E402


def _p(**kw):
    base = dict(name="a", repo="o/a", workdir="/root/a")
    base.update(kw)
    return projects.Project(**base)


def test_one_line_per_project_in_list_order():
    out = projects.format_projects([_p(name="eins"), _p(name="zwei", repo="o/zwei", workdir="/root/zwei")])
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("eins") and lines[1].startswith("zwei")


def test_line_names_repo_merge_mode_and_hotspot():
    line = projects.format_projects([_p(name="x", repo="o/x", auto_merge=True, hotspot="a/main.py")])
    assert line == "x (o/x): Merge automatisch, Hotspot a/main.py"


def test_manual_merge_and_no_hotspot():
    line = projects.format_projects([_p(name="y", repo="o/y", auto_merge=False, hotspot=None)])
    assert line == "y (o/y): Merge manuell, kein Hotspot"


def test_empty_list_gives_empty_string():
    assert projects.format_projects([]) == ""
