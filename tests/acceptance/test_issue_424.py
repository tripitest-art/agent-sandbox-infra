import importlib.util
import itertools
import json
import sys
from pathlib import Path

_TICK_PATH = Path(__file__).resolve().parents[2] / "sandbox" / "worker_tick.py"

FREIE_ALIASE = ("coder-mimo-free", "coder-deepseek-free", "coder-nemotron-free",
                "coder-minimax-free", "coder-beam-free")
HINWEIS = "Billig-Slot: kein freies Modell antwortet, weiche auf coder-go (OpenCode Go, Abo-Quote) aus."

_ZAEHLER = itertools.count()


def _lade(monkeypatch, tmp_path, ausweich):
    """worker_tick frisch als Billig-Slot laden; `ausweich` None löscht LITELLM_BILLIG_AUSWEICH."""
    einst = tmp_path / "agent-settings.json"
    einst.write_text(json.dumps({"LITELLM_MODEL_BILLIG": "coder-mimo-free"}), encoding="utf-8")
    monkeypatch.setenv("WORKER_SLOT", "2")
    monkeypatch.setenv("AGENT_SETTINGS", str(einst))
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.delenv("LITELLM_FALLBACKS", raising=False)
    if ausweich is None:
        monkeypatch.delenv("LITELLM_BILLIG_AUSWEICH", raising=False)
    else:
        monkeypatch.setenv("LITELLM_BILLIG_AUSWEICH", ausweich)
    name = f"worker_tick_billig_go_{next(_ZAEHLER)}"
    spec = importlib.util.spec_from_file_location(name, _TICK_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(mod, "CLAIM_DIR", str(tmp_path / "claims"))
    monkeypatch.setattr(mod, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(mod, "SLOT_DIR", str(tmp_path / "slots"))
    return mod


def _probe_nur(w, monkeypatch, antwortet):
    """probe_model fälschen: nur Aliase in `antwortet` antworten; die Reihenfolge wird mitgeschrieben."""
    geprobt = []

    def probe(model):
        geprobt.append(model)
        return None if model in antwortet else "HTTP 403"

    monkeypatch.setattr(w, "probe_model", probe)
    return geprobt


def test_ohne_variable_bleibt_die_kette_frei(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, None)
    assert w.IS_BILLIG_SLOT
    assert w.BILLIG_AUSWEICHMODELL is None
    assert w.billig_kette(w.BILLIG_MODEL) == list(FREIE_ALIASE[1:])
    assert w.billig_kette(w.BILLIG_MODEL) == w.billig_fallbacks(w.BILLIG_MODEL)
    assert w.abo_ausweichen("coder-mimo-free", "coder-go") is None


def test_mit_coder_go_kommt_go_als_letztes(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, " coder-go ")
    assert w.BILLIG_AUSWEICH_ERLAUBT == ("coder-go",)
    assert w.BILLIG_AUSWEICHMODELL == "coder-go"
    kette = w.billig_kette(w.BILLIG_MODEL)
    assert kette == [*FREIE_ALIASE[1:], "coder-go"]
    for alias in kette:
        assert alias not in w.BEZAHLTE_MODELLE, "nie ein bezahltes Modell im Billig-Slot"
    assert w.billig_fallbacks(w.BILLIG_MODEL) == list(FREIE_ALIASE[1:]), "billig_fallbacks bleibt unverändert"


def test_andere_werte_der_variable_gelten_nicht(monkeypatch, tmp_path):
    for wert in ("coder", "coder-or", "coder-pro-go", "coder-kimi-go", "coder-mimo-free", "   ", ""):
        w = _lade(monkeypatch, tmp_path, wert)
        assert w.BILLIG_AUSWEICHMODELL is None, wert
        assert w.billig_kette(w.BILLIG_MODEL) == list(FREIE_ALIASE[1:]), wert


def test_ungueltiger_primary_ergibt_leere_kette(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "coder-go")
    assert w.billig_kette(None) == []
    assert w.billig_kette("") == []
    assert w.billig_kette("coder-nichtda") == []
    assert w.billig_kette("coder-go") == []


def test_choose_model_landet_auf_go_wenn_alle_freien_403_melden(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "coder-go")
    geprobt = _probe_nur(w, monkeypatch, {"coder-go"})
    model, failed = w.choose_model(w.BILLIG_MODEL, w.billig_kette(w.BILLIG_MODEL))
    assert model == "coder-go"
    assert geprobt == [*FREIE_ALIASE, "coder-go"], "zuerst alle freien, Go zuletzt"
    assert failed == [f"{a}: HTTP 403" for a in FREIE_ALIASE]


def test_freies_modell_hat_vorrang_vor_go(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "coder-go")
    geprobt = _probe_nur(w, monkeypatch, {"coder-nemotron-free", "coder-go"})
    model, _ = w.choose_model(w.BILLIG_MODEL, w.billig_kette(w.BILLIG_MODEL))
    assert model == "coder-nemotron-free"
    assert "coder-go" not in geprobt


def test_hinweiszeile(monkeypatch, tmp_path):
    w = _lade(monkeypatch, tmp_path, "coder-go")
    assert w.abo_ausweichen("coder-mimo-free", "coder-go") == HINWEIS
    assert w.abo_ausweichen("coder-mimo-free", "coder-deepseek-free") is None
    assert w.abo_ausweichen("coder-go", "coder-go") is None


def _main_bis_zum_claim(w, monkeypatch):
    """main() bis zur Modellwahl laufen lassen; der Claim scheitert, also startet kein Agent."""
    monkeypatch.setattr(w, "BACKEND", "litellm")
    monkeypatch.setattr(w, "litellm_ready", lambda: None)
    monkeypatch.setattr(w, "main_is_red", lambda state: False)
    monkeypatch.setattr(w, "main_is_red_local", lambda state: False)
    monkeypatch.setattr(w, "verwaiste_melden", lambda state, now=None: [])
    monkeypatch.setattr(w, "prepare_workdir", lambda: None)
    monkeypatch.setattr(w, "key_info", lambda: None)
    monkeypatch.setattr(w, "pick_job", lambda state: ("issue501", f"Du bist Worker: {w.WORKER}.", None, None))
    claims = []
    monkeypatch.setattr(w, "claim_job", lambda key: claims.append(key) or False)

    def kein_gh(*args):
        raise AssertionError(f"unerwarteter gh-Aufruf: {args}")

    monkeypatch.setattr(w, "gh", kein_gh)
    assert w.main() == 0
    return claims


def test_main_probt_go_zuletzt_und_meldet_das_ausweichen(monkeypatch, tmp_path, capsys):
    w = _lade(monkeypatch, tmp_path, "coder-go")
    geprobt = _probe_nur(w, monkeypatch, {"coder-go"})
    claims = _main_bis_zum_claim(w, monkeypatch)
    assert geprobt == [*FREIE_ALIASE, "coder-go"]
    assert claims == ["issue501"], "nach dem Ausweichen geht der Takt bis zum Claim"
    assert w.ACTIVE_MODEL == "coder-go"
    assert HINWEIS in capsys.readouterr().out
    gespeichert = json.loads(Path(w.STATE).read_text(encoding="utf-8")) if Path(w.STATE).exists() else {}
    assert "_probe_cache" not in gespeichert, "das Ausweichmodell wird nie als Primärprobe gecacht"


def test_main_ohne_variable_wartet_wie_bisher(monkeypatch, tmp_path, capsys):
    w = _lade(monkeypatch, tmp_path, None)
    geprobt = _probe_nur(w, monkeypatch, {"coder-go"})
    claims = _main_bis_zum_claim(w, monkeypatch)
    assert geprobt == list(FREIE_ALIASE), "ohne Variable wird coder-go nie geprobt"
    assert claims == [], "kein Claim, wenn kein freies Modell antwortet"
    out = capsys.readouterr().out
    assert "kein Modell antwortet" in out
    assert "OpenCode Go, Abo-Quote" not in out
