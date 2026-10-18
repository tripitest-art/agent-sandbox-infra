from pathlib import Path

_INSTALL_SH = Path(__file__).resolve().parents[2] / "sandbox" / "install.sh"
_QWEN_SERVICE = Path(__file__).resolve().parents[2] / "sandbox" / "systemd" / "qwen-webhook.service"


def test_install_sh_enthaelt_keine_ha_dienste_oder_secrets():
    content = _INSTALL_SH.read_text(encoding="utf-8")
    assert "ha-status.service" not in content
    assert "ha-status.timer" not in content
    assert "qwen-webhook.service" not in content
    assert "HA_WEBHOOK_URL" not in content
    assert "HA_STATUS_URL" not in content


def test_qwen_webhook_service_datei_existiert_nicht_mehr():
    assert not _QWEN_SERVICE.exists()
