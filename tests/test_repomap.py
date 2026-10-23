"""Tests für sandbox/repomap.py: Symbole, 1-Hop-Importe, Grenzen und Fehlerfälle (#227)."""

from pathlib import Path

from sandbox import repomap


def _schreibe(tmp_path: Path, name: str, text: str) -> None:
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_syntaxfehler_kapitel_faellt_weg_ohne_exception(tmp_path):
    _schreibe(tmp_path, "gut.py", "def gut():\n    pass\n")
    _schreibe(tmp_path, "kaputt.py", "def kaputt(:\n    pass\n")
    karte = repomap.build_repomap(tmp_path, ["gut.py", "kaputt.py"])
    assert "def gut" in karte
    assert "kaputt.py" not in karte


def test_nicht_dekodierbare_datei_faellt_weg_ohne_exception(tmp_path):
    (tmp_path / "bytes.py").write_bytes(b"\xff\xfe\x00def x(): pass\n")
    _schreibe(tmp_path, "gut.py", "def gut():\n    pass\n")
    karte = repomap.build_repomap(tmp_path, ["gut.py", "bytes.py"])
    assert "def gut" in karte
    assert "bytes.py" not in karte


def test_kuerzen_an_zeilengrenzen(tmp_path):
    _schreibe(tmp_path, "viele.py",
              "\n".join(f"def f_{i:03d}():\n    pass\n" for i in range(100)))
    karte = repomap.build_repomap(tmp_path, ["viele.py"], max_tokens=20)   # 80 Zeichen
    assert len(karte) <= 80
    zeilen = karte.splitlines()
    assert zeilen[0] == "viele.py:"
    for zeile in zeilen[1:]:
        assert zeile.startswith("def f_") and zeile.endswith("()")


def test_null_tokens_ergibt_leere_map(tmp_path):
    _schreibe(tmp_path, "foo.py", "def foo():\n    pass\n")
    assert repomap.build_repomap(tmp_path, ["foo.py"], max_tokens=0) == ""


def test_1_hop_wird_nicht_weiter_verfolgt(tmp_path):
    _schreibe(tmp_path, "c.py", "def c_tief():\n    pass\n")
    _schreibe(tmp_path, "b.py", "import c\n\n\ndef b_mittel():\n    pass\n")
    _schreibe(tmp_path, "a.py", "import b\n\n\ndef a_oben():\n    pass\n")
    karte = repomap.build_repomap(tmp_path, ["a.py"])
    assert "def a_oben" in karte
    assert "def b_mittel" in karte
    assert "def c_tief" not in karte
    assert "c.py" not in karte


def test_importkapitel_ist_markiert(tmp_path):
    _schreibe(tmp_path, "helpers.py", "def hilfe():\n    pass\n")
    _schreibe(tmp_path, "foo.py", "from helpers import hilfe\n")
    karte = repomap.build_repomap(tmp_path, ["foo.py"])
    assert "[importiert] helpers.py:" in karte
    assert "from helpers import hilfe" in karte
