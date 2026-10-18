import importlib.util
import sys
from pathlib import Path

_P_DASHBOARD = Path(__file__).resolve().parents[2] / "sandbox" / "dashboard.py"
_spec_dash = importlib.util.spec_from_file_location("dashboard_autark", _P_DASHBOARD)
dash = importlib.util.module_from_spec(_spec_dash)
sys.modules["dashboard_autark"] = dash
_spec_dash.loader.exec_module(dash)

_P_HA = Path(__file__).resolve().parents[2] / "sandbox" / "ha_status.py"
_spec_ha = importlib.util.spec_from_file_location("ha_status_reexport", _P_HA)
ha = importlib.util.module_from_spec(_spec_ha)
sys.modules["ha_status_reexport"] = ha
_spec_ha.loader.exec_module(ha)

_P_QWEN = Path(__file__).resolve().parents[2] / "sandbox" / "qwen_webhook.py"
_spec_qwen = importlib.util.spec_from_file_location("qwen_webhook_reexport", _P_QWEN)
qwen = importlib.util.module_from_spec(_spec_qwen)
sys.modules["qwen_webhook_reexport"] = qwen
_spec_qwen.loader.exec_module(qwen)


def test_dashboard_importiert_weder_ha_status_noch_qwen_webhook():
    with open(_P_DASHBOARD, encoding="utf-8") as f:
        src = f.read()
    assert "import ha_status" not in src and "from ha_status" not in src
    assert "import qwen_webhook" not in src and "from qwen_webhook" not in src


def test_dashboard_stellt_build_status_und_helfer_direkt_bereit():
    assert callable(dash.build_status)
    assert callable(dash.summarize_project)
    assert callable(dash.summarize_budget)
    assert callable(dash.strip_ansi)
    assert callable(dash.pid_is_active)
    assert callable(dash.read_slots)
    assert hasattr(dash, "Follower")
    assert hasattr(dash, "BUDGET_KEYS")
    assert hasattr(dash, "DEFAULT_LITELLM")


def test_ha_status_und_qwen_webhook_reexportieren_funktionen():
    assert ha.build_status is dash.build_status
    assert ha.summarize_project is dash.summarize_project
    assert qwen.strip_ansi is dash.strip_ansi
    assert qwen.pid_is_active is dash.pid_is_active
    assert qwen.read_slots is dash.read_slots
