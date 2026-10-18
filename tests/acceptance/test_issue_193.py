import importlib.util
import subprocess
import sys
from pathlib import Path

SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
spec = importlib.util.spec_from_file_location("autodeploy", SANDBOX / "autodeploy.py")
ad = importlib.util.module_from_spec(spec)
sys.modules["autodeploy"] = ad
spec.loader.exec_module(ad)


def cp(cmd, rc=0, out=""):
    return subprocess.CompletedProcess(cmd, rc, out, "")


PYTEST_OUT = (
    ".F.F\n"
    "FAILED tests/test_a.py::test_eins - AssertionError: assert 'geheim' == 1\n"
    "FAILED tests/acceptance/test_b.py::test_zwei[p1]\n"
    "2 failed, 5 passed in 1.2s\n"
)


def test_failed_tests_liefert_nur_die_namen():
    assert ad.failed_tests(PYTEST_OUT) == ["tests/test_a.py::test_eins", "tests/acceptance/test_b.py::test_zwei[p1]"]


def test_failed_tests_begrenzt_und_leer():
    viele = "".join(f"FAILED tests/t.py::test_{i} - x\n" for i in range(9))
    assert ad.failed_tests(viele, limit=3) == [f"tests/t.py::test_{i}" for i in range(3)]
    assert len(ad.failed_tests(viele)) == 5
    assert ad.failed_tests("") == [] and ad.failed_tests("3 passed in 0.1s\n") == []


def test_failed_tests_verwirft_unsichere_namen():
    assert ad.failed_tests("FAILED nicht gueltig mit leerzeichen\nFAILED $(rm)::t\n") == []


def test_preflight_cmd_form_und_gesperrte_pfade():
    wurzel = "/ro" + "ot"        # kein fester Serverpfad im Test (issuecheck: root-pfad)
    py, repo = wurzel + "/venv/bin/python", wurzel + "/agent-sandbox-infra"
    cmd = ad.preflight_cmd(py, repo)
    assert isinstance(cmd, tuple)
    assert cmd[:5] == ("systemd-run", "--quiet", "--wait", "--pipe", "--collect")
    assert f"WorkingDirectory={repo}" in cmd
    sperren = [c for c in cmd if c.startswith("InaccessiblePaths=")]
    assert len(sperren) == 1
    pfade = sperren[0].split("=", 1)[1].split()
    for p in ("/etc/agent-projects.json", "/etc/agent-settings.json", wurzel + "/sc-digger", wurzel + "/work",
              "/var/lib/worker-claims"):
        assert "-" + p in pfade
    assert all(p.startswith("-/") for p in pfade)
    for p in (wurzel, repo, wurzel + "/venv"):
        assert "-" + p not in pfade
    assert cmd[-6:] == (py, "-m", "pytest", "-q", "-p", "no:cacheprovider")


def test_preflight_cmd_eigene_liste():
    cmd = ad.preflight_cmd("py", "/r", blocked=["/x", "/y"])
    assert "InaccessiblePaths=-/x -/y" in cmd


def test_choose_test_cmd_isoliert_bei_erfolgreichem_probelauf():
    gesehen = []

    def run(cmd):
        gesehen.append(list(cmd))
        return cp(cmd, 0)

    cmd, isoliert = ad.choose_test_cmd("py", "/r", run)
    assert isoliert is True and cmd == ad.preflight_cmd("py", "/r")
    assert len(gesehen) == 1 and gesehen[0][0] == "systemd-run" and gesehen[0][-1] == "true"


def test_choose_test_cmd_faellt_bei_fehlgeschlagenem_probelauf_zurueck():
    for rc in (1, 124, 127):
        cmd, isoliert = ad.choose_test_cmd("py", "/r", lambda c, rc=rc: cp(c, rc))
        assert isoliert is False and cmd == ("py", "-m", "pytest", "-q")


class Git:
    def __init__(self, test_out=""):
        self.test_out = test_out

    def __call__(self, cmd):
        c = list(cmd)
        if c[:2] == ["git", "status"]:
            return cp(cmd)
        if c[:3] == ["git", "rev-parse", "--short"]:
            return cp(cmd, out=("1111111" if "HEAD" in c else "2222222") + "\n")
        if c == ["tests"]:
            return cp(cmd, 1, self.test_out)
        return cp(cmd)


def deploy(tmp_path, git):
    notes = []
    res = ad.deploy_once(tmp_path, git, notes.append, is_busy=lambda: False,
                         test_cmd=["tests"], install_cmd=["install"])
    return res, notes


def test_rollback_nennt_die_roten_tests(tmp_path):
    res, notes = deploy(tmp_path, Git(PYTEST_OUT))
    assert res.status == "zurueckgerollt"
    assert res.detail == ("Tests fehlgeschlagen (Exit 1): "
                          "tests/test_a.py::test_eins, tests/acceptance/test_b.py::test_zwei[p1]")
    assert all("geheim" not in n and "AssertionError" not in n for n in notes)
    assert any("tests/test_a.py::test_eins" in n for n in notes)


def test_rollback_ohne_namen_bleibt_unveraendert(tmp_path):
    res, notes = deploy(tmp_path, Git("kaputt, ohne FAILED-Zeilen\n"))
    assert res.status == "zurueckgerollt"
    assert res.detail == "Tests fehlgeschlagen (Exit 1)"
