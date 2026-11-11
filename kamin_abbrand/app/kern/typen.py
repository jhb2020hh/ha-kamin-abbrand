"""Gemeinsame Datentypen des Kerns. Nur Standardbibliothek, kein HA-Import.

Der Kern kennt keine Uhr und kein I/O: Alles kommt über `Eingaben` herein.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

STANDBY = "Standby"
TUER_OFFEN = "Tür offen"
ANHEIZEN = "Anheizen"
ABBRAND = "Abbrand"
AUSBRAND = "Ausbrand"
GLUT = "Glut"
ABKUEHLEN = "Abkühlen"
ANHEIZFEHLER = "Anheizfehler"
STOERUNG = "Störung"
WARTUNG = "Wartung"
PHASEN = (STANDBY, TUER_OFFEN, ANHEIZEN, ABBRAND, AUSBRAND, GLUT, ABKUEHLEN,
          ANHEIZFEHLER, STOERUNG, WARTUNG)

# Regelstände: paritaet = 1:1 wie die Live-Automation, neu = mit den offenen Themen
PARITAET = "paritaet"
NEU = "neu"

# Auslöser eines Laufs, wie die Trigger-IDs der Live-Automation
TICK = "tick"
TUER = "tuer"
WARTUNG_AN = "wartung_an"
WARTUNG_AUS = "wartung_aus"
BEDIENUNG = "bedienung"
SENSOR_WEG = "sensor_weg"
START = "start"

# Aktionen, die die Anbindung ausführt (im Parallelbetrieb nur protokolliert)
AKTION_SNIFFER_RESTART = "sniffer_restart"
AKTION_WARTUNG_AUS = "wartung_aus"

# Signalzustand des Abgaswerts (nur Regelstand neu aussagekräftig)
SIGNAL_FRISCH = "frisch"
SIGNAL_STILL = "still"
SIGNAL_STUMM = "stumm"


@dataclass
class Parameter:
    """Feldname = Suffix des Helfers input_number.kamin_abbrand_<feld>.

    Einheiten wie im Helfer (°C, %, K, min, K/min, s). Vorgaben = Live-Stand 06.11.2026.
    """
    t_kalt: float = 50
    t_abbrandstart: float = 160
    t_ziel: float = 220
    t_glut: float = 130
    t_abkuehlen: float = 80
    t_uebertemperatur: float = 400
    t_wasser_max: float = 85
    gradient_start: float = 3
    ausbrand_abfall: float = 25
    startanstieg: float = 20
    startfenster: float = 10            # min
    anheizen_mindestdauer: float = 15   # min
    anheizen_mindestdauer_nachlegen: float = 2  # min
    anheizen_mindestdauer_feuer: float = 2      # min
    anheizen_timeout: float = 40        # min
    glut_max_dauer: float = 120         # min
    abkuehlen_max_dauer: float = 180    # min
    wartung_timeout: float = 60         # min
    entgasung_dauer: float = 10         # min
    klappe_min_abbrand: float = 35
    klappe_max_abbrand: float = 100
    klappe_min_entgasung: float = 45
    klappe_schliessrate: float = 10     # % je 30 s
    klappe_glut: float = 10
    klappe_wartung: float = 100
    klappe_manuell: float = 100
    klappe_uebertemperatur: float = 25
    klappe_wasser: float = 40
    klappe_anheizen_feuer: float = 60
    klappe_kaltstart: float = 100
    t_kaltstart_ende: float = 220       # °C
    regler_verstaerkung: float = 0.5
    regler_nachstellzeit: float = 7     # min
    regler_vorhaltung: float = 0
    regler_i_start_kalt: float = 70
    regler_i_start_heiss: float = 40
    puffer_voll: float = 60
    fuehler_timeout: float = 600        # s
    feuer_twt_anstieg: float = 3        # K in 10 min

    @classmethod
    def feldnamen(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))


@dataclass
class Eingaben:
    """Messwerte und Bedienzustände eines Laufs."""
    jetzt: float                        # Unix-Zeit
    t: Optional[float]                  # Abgas, None = kein Zahlenwert (unavailable/unknown)
    t_alter_s: float = 0.0              # Sekunden seit dem letzten gemeldeten Abgaswert
    g: float = 0.0                      # Gradient K/min (HA-Helfer)
    tmax3: Optional[float] = None       # Maximum 3 min (HA-Helfer), None = t verwenden
    tuer_offen: Optional[bool] = False  # None = Kontakt unbekannt: weder offen noch zu
    twt_roh: Optional[float] = None     # T.WT, roh; ungültig/None wird im Kern zu 0
    twt_anstieg: float = 0.0            # K in 10 min
    puffer_unten: float = 0.0
    wartung: bool = False
    handbetrieb: bool = False
    ausloeser: str = TICK
    # nur Regelstand neu
    dp_alter_s: Optional[float] = None  # Sekunden seit der letzten Druckänderung
    can_alter_s: Optional[float] = None  # Sekunden seit dem letzten CAN-Frame (nach dem Flashen)
    tuerkontakt_vorhanden: bool = True


@dataclass
class Zustand:
    """Entspricht den Helfern, die die Live-Automation als Gedächtnis nutzt."""
    phase: str = STANDBY
    phase_seit: float = 0.0             # ganze Sekunden wie input_datetime
    t_maximum: float = 0.0
    t_phasenstart: float = 0.0
    i_anteil: float = 50.0
    klappe_soll: int = 0
    # nur Regelstand neu
    letzte_tuer: Optional[float] = None  # Zeitpunkt der letzten Türöffnung
    t_tief: Optional[float] = None       # tiefste Temperatur seit Phasenbeginn
    letzter_lauf: Optional[float] = None
    klappe_genau: Optional[float] = None  # Soll ungerundet, damit die Schließrate je 5 s stimmt


@dataclass
class Meldung:
    titel: str
    text: str


@dataclass
class Ergebnis:
    zustand: Zustand
    klappe: int                         # Soll in %
    regel: Optional[str] = None         # Alias der gefeuerten Phasenregel
    uebergang: Optional[tuple[str, str]] = None  # (alt, neu)
    meldungen: list[Meldung] = field(default_factory=list)
    aktionen: list[str] = field(default_factory=list)
    signal: str = SIGNAL_FRISCH
