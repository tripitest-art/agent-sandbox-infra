import importlib.util
import itertools
import json
import sys
from pathlib import Path

_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

GO_ALIASE = ("coder-go", "coder-kimi-go", "coder-pro-go")
BISHER = ["coder", "coder-pro", "coder-fast", "coder-or", "coder-free", "coder-glm", "coder-qwen", "coder-nvidia"]

_ZAEHLER = itertools.count()


def _load(name):
    modname = f"{name}_go_wahl_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(modname, _SANDBOX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _einstellungen(monkeypatch, tmp_path, werte):
    p = tmp_path / "agent-settings.json"
    p.write_text(json.dumps(werte), encoding="utf-8")
    monkeypatch.setenv("AGENT_SETTINGS", str(p))
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.delenv("LITELLM_FALLBACKS", raising=False)
    monkeypatch.delenv("LITELLM_MODEL_BILLIG", raising=False)
    monkeypatch.setenv("WORKER_SLOT", "1")
    monkeypatch.setenv("BACKEND", "litellm")
    monkeypatch.delenv("WORKER", raising=False)
    return p


def test_erlaubt_liste_haengt_die_go_aliase_an():
    s = _load("agent_settings")
    assert s.SPECS["LITELLM_MODEL"]["erlaubt"] == [*BISHER, *GO_ALIASE]


def test_go_aliase_sind_gueltig_reviewer_go_nicht():
    s = _load("agent_settings")
    for alias in GO_ALIASE:
        assert s.validate("LITELLM_MODEL", alias) == alias
    for falsch in ("reviewer-go", "coder-qwen-go", "coder-mimo", "coder-go "):
        try:
            s.validate("LITELLM_MODEL", falsch)
        except s.SettingError:
            continue
        raise AssertionError(f"{falsch!r} darf nicht erlaubt sein")


def test_datei_mit_coder_go_wird_gelesen_und_gespeichert(monkeypatch, tmp_path):
    p = _einstellungen(monkeypatch, tmp_path, {"LITELLM_MODEL": "coder-go"})
    s = _load("agent_settings")
    assert s.value("LITELLM_MODEL", "coder") == "coder-go"
    assert s.aktuelle_werte()["LITELLM_MODEL"] == {"wert": "coder-go", "quelle": "datei"}
    s.save({"LITELLM_MODEL": "coder-kimi-go"})
    assert json.loads(p.read_text(encoding="utf-8"))["LITELLM_MODEL"] == "coder-kimi-go"


def test_worker_tick_eskaliert_mit_coder_go_aus_der_datei(monkeypatch, tmp_path):
    _einstellungen(monkeypatch, tmp_path, {"LITELLM_MODEL": "coder-go"})
    w = _load("worker_tick")
    assert w.LITELLM_MODEL == "coder-go"
    assert w.ACTIVE_MODEL == "coder-go"
    assert w.WORKER == "DeepSeek (LiteLLM coder-go, OpenCode Go)"
    assert w.model_for_attempt(0) == "coder-go"
    assert w.model_for_attempt(1) == "coder-kimi-go"
    assert w.model_for_attempt(2) == "coder-pro-go"


def test_go_aliase_liegen_nicht_in_der_reviewer_familie():
    w = _load("worker_tick")
    r = _load("review_tick")
    for alias in GO_ALIASE:
        familie = r.model_family(w.worker_name(alias))
        assert familie is not None and familie != "glm", f"Regel 6: {alias} -> {familie!r}"
