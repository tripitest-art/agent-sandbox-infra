from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    p = _ROOT / name
    assert p.is_file(), f"{name} fehlt"
    return p.read_text(encoding="utf-8")


def test_security_md_meldeweg():
    t = _read("SECURITY.md")
    assert "security/advisories" in t or "Private vulnerability reporting" in t
    assert "Zugangsdaten" in t
    assert "@" not in t, "keine E-Mail-Adresse im öffentlichen Repo"


def test_license_ist_mit():
    t = _read("LICENSE")
    assert t.startswith("MIT License")
    assert "Copyright (c) 2026 Stephan Voigt" in t
    assert "Permission is hereby granted, free of charge" in t


def test_readme_verweist():
    r = _read("README.md")
    assert "SECURITY.md" in r and "LICENSE" in r
