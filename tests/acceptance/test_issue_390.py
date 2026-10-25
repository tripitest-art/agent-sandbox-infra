from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def test_ha_und_webhook_dateien_vollstaendig_entfernt():
    geloeschte_dateien = [
        ROOT / "sandbox" / "ha_status.py",
        ROOT / "sandbox" / "qwen_webhook.py",
        ROOT / "sandbox" / "systemd" / "ha-status.service",
        ROOT / "sandbox" / "systemd" / "ha-status.timer",
        ROOT / "sandbox" / "systemd" / "qwen-webhook.service",
        ROOT / "tests" / "test_ha_status.py",
        ROOT / "tests" / "test_qwen_webhook.py",
    ]
    for datei in geloeschte_dateien:
        assert not datei.exists(), f"{datei.name} existiert noch und muss geloescht werden"

def test_install_sh_ohne_ha_status_referenzen():
    install_sh = ROOT / "sandbox" / "install.sh"
    content = install_sh.read_text(encoding="utf-8")
    assert "ha-status" not in content
    assert "qwen-webhook" not in content
