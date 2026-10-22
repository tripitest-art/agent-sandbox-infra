"""Grundlegende Git-Operationen auf einem Arbeitsbaum als eigenständiges Modul (#220).

Der Aufruf kommt als `run` herein: ein `Runner` bekommt ausschließlich die Argumentliste und
führt sie im Arbeitsbaum aus (der Aufrufer bindet das Verzeichnis, etwa mit `cwd`). Das Modul
kennt weder Projekt noch Zustand und startet nichts von selbst (Regel 8, Phase-1
Modularisierung); in Tests ersetzt ein Fake den Aufruf. `workdir` wird deshalb nur für
Meldungen und als Hinweis für den Leser geführt, nicht an `run` weitergereicht.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Sequence

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def clean_worktree(workdir: str | Path, run: Runner) -> bool:
    """Setzt den Arbeitsbaum deterministisch zurück: `git reset --hard HEAD`, `git clean -fd`.

    Beide Befehle laufen immer, auch wenn der erste scheitert: Ein Rest von `reset` soll `clean`
    nicht verhindern, und der Rückgabewert sagt, ob beide sauber durchliefen. Nach einem Abbruch
    des Agenten darf kein halb angewandter Stand den nächsten Lauf verfälschen.
    """
    sauber = True
    for argv in (["git", "reset", "--hard", "HEAD"], ["git", "clean", "-fd"]):
        if run(argv).returncode != 0:
            sauber = False
    return sauber


def get_current_head(workdir: str | Path, run: Runner) -> str:
    """Aktueller Commit-Hash (`git rev-parse HEAD`), getrimmt.

    Wirft RuntimeError bei Fehler: Ohne Hash kann der Aufrufer nicht prüfen, ob der Agent
    committet hat, und ein leerer Rückgabewert würde das als „nichts passiert“ verschleiern.
    """
    res = run(["git", "rev-parse", "HEAD"])
    if res.returncode != 0:
        grund = (res.stderr or "").strip()
        raise RuntimeError(
            f"git rev-parse HEAD in {workdir} fehlgeschlagen "
            f"(Exitcode {res.returncode}){': ' + grund if grund else ''}")
    return (res.stdout or "").strip()


def branch_exists(workdir: str | Path, branch: str, run: Runner) -> bool:
    """True, wenn der lokale Zweig `branch` existiert (`git rev-parse --verify`).

    `--quiet` unterdrückt die Fehlermeldung von git, die Antwort ist allein der Exitcode; der
    volle Ref-Pfad `refs/heads/<branch>` verhindert, dass ein Tag gleichen Namens zählt.
    """
    res = run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    return res.returncode == 0
