from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_ha_dateien_und_ordner_existieren_nicht_mehr():
    assert not (_ROOT / "sandbox" / "ha_status.py").exists()
    assert not (_ROOT / "sandbox" / "qwen_webhook.py").exists()
    assert not (_ROOT / "homeassistant").exists()
    assert not (_ROOT / "tests" / "test_ha_status.py").exists()
    assert not (_ROOT / "tests" / "test_qwen_webhook.py").exists()


def test_install_sh_referenziert_kein_qwen_webhook():
    content = (_ROOT / "sandbox" / "install.sh").read_text(encoding="utf-8")
    assert "qwen_webhook.py" not in content
