import re
from pathlib import Path

HA = Path(__file__).resolve().parents[2] / "homeassistant"
SENSORS = (HA / "configuration_sensors.yaml").read_text(encoding="utf-8")
DASH = (HA / "dashboard_card.yaml").read_text(encoding="utf-8")
README = (HA / "README.md").read_text(encoding="utf-8")


def test_status_sensor_ist_ein_webhook_trigger_mit_eigener_id():
    assert re.search(r"webhook_id:\s*!secret\s+ha_status_webhook_id\b", SENSORS), \
        "der Status-Webhook braucht eine eigene ID in secrets.yaml (ha_status_webhook_id)"
    block = SENSORS[SENSORS.index("ha_status_webhook_id"):]
    block = block[:block.index("# 3.")] if "# 3." in block else block
    assert "local_only: true" in block.split("unique_id")[0], "Webhook nur aus dem Heimnetz"
    assert "allowed_methods: [POST]" in block.split("unique_id")[0]
    assert re.search(r"unique_id:\s*agenten_workflow_status\b", SENSORS)
    assert re.search(r'name:\s*"Agenten Workflow Status"', SENSORS)


def test_status_sensor_zeigt_die_felder_des_json():
    block = SENSORS[SENSORS.index("agenten_workflow_status"):]
    block = block.split("# 3.")[0]
    assert "trigger.json.braucht_stephan" in block, "Zustand = Zahl der wartenden PRs"
    for feld in ("projekte", "budgets", "zeit"):
        assert re.search(rf"^\s*{feld}:\s*.*trigger\.json\.{feld}", block, re.M), f"Attribut {feld} fehlt"


def test_live_log_und_slot_sensoren_bleiben():
    assert "qwen_worker_webhook_id" in SENSORS
    for name in ("qwen_worker_log", "qwen_worker_slot_1", "qwen_worker_slot_2"):
        assert f"unique_id: {name}" in SENSORS


def test_keine_webhook_id_im_repo():
    for text in (SENSORS, DASH, README):
        assert not re.search(r"api/webhook/[0-9A-Za-z_-]{16,}", text), "echte Webhook-ID im Text (Regel 5)"


def test_dashboard_liest_den_status_sensor():
    assert DASH.count("sensor.agenten_workflow_status") >= 3, "Matrix, Braucht-Stephan und Budgets lesen ihn"
    for attr in ("projekte", "budgets"):
        assert re.search(rf"state_attr\(\s*'sensor\.agenten_workflow_status'\s*,\s*'{attr}'", DASH), attr
    for feld in ("bereit", "in-arbeit", "blockiert", "rot", "laufend", "prozent", "fehler"):
        assert feld in DASH, f"Feld {feld} wird nicht angezeigt"


def test_bestehende_karten_und_buttons_bleiben():
    for rest in ("sensor.llm_gaming_pc", "sensor.qwen_worker_log", "sensor.qwen_worker_slot_1",
                 "sensor.qwen_worker_slot_2", "rest_command.wake_llm_pc", "shell_command.start_qwen_worker"):
        assert rest in DASH, rest
    assert DASH.index("sensor.agenten_workflow_status") < DASH.index("shell_command.start_qwen_worker"), \
        "die neuen Karten stehen vor den Steuerungs-Buttons"


def test_readme_erklaert_einrichtung():
    assert "ha_status_webhook_id" in README
    assert "HA_STATUS_URL" in README
    assert "template.reload" in README
    assert "Workflow-Status" in README


def test_changelog_nennt_den_status_sensor():
    changelog = (HA.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "Workflow-Status" in unreleased
