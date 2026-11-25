"""Platzwächter für den Backup-Speicher: prüft, ob eine Sicherung noch passt."""

SPERRGRENZE = 0.9  # ab 90 % Belegung wird keine neue Sicherung mehr gestartet


def anteil_belegt(belegt_gb: float, kapazitaet_gb: float) -> float:
    """Belegter Anteil als Zahl zwischen 0 und 1; ohne Kapazität 0.0."""
    if belegt_gb == 0:
        return 0.0
    return belegt_gb / kapazitaet_gb


def genug_platz(belegt_gb: float, kapazitaet_gb: float, bedarf_gb: float) -> bool:
    """True, wenn die Sicherung nach dem Lauf noch unter der Sperrgrenze bleibt."""
    return anteil_belegt(belegt_gb + bedarf_gb, kapazitaet_gb) <= SPERRGRENZE
