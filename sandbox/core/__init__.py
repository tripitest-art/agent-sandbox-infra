"""Eigenständige Schnittstellen des Takts: GitHub (`gh_client`) und LiteLLM (`litellm_client`).

Die Module kennen weder Projekt noch Zustand: Sie bekommen ihre Angaben als Argumente und
werfen nichts, was der Aufrufer nicht erwartet. Damit lassen sie sich aus `worker_tick.py` und
`review_tick.py` nutzen, ohne dass beide die Aufrufe doppelt pflegen (Regel 8, Ausbaustufe
Phase-1 Modularisierung).
"""
