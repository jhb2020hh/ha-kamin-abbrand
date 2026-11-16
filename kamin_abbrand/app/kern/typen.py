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
QUITTIEREN = "quittieren"           # Fehler von Hand bestätigt (nur Regelstand neu)

# Aktionen, die die Anbindung ausführt (im Parallelbetrieb nur protokolliert)
AKTION_SNIFFER_RESTART = "sniffer_restart"
AKTION_WARTUNG_AUS = "wartung_aus"
AKTION_BRENNSTOFF_HOLZ = "brennstoff_holz"   # Brikett-Nachlauf vorbei: Wahl zurück auf Holz

# Brennstoff je Auflage, von Hand gewählt (nur Regelstand neu). Die Abgastemperatur trennt
# Holz und Braunkohlebriketts beim Arena Aqua 2.0 nicht (DIBt Z-43.11-408, Tab. 1).
HOLZ = "Holz"
BRIKETTS = "Briketts"
BRENNSTOFFE = (HOLZ, BRIKETTS)

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
    # Abgasfühler (nur Regelstand neu): 1 = LEDA LUC, meldet nur Änderungen, Druck aus demselben Gerät;
    # 2 = eigener Fühler, meldet regelmäßig. Dann zählt nur das Alter des Abgaswerts.
    fuehler_art: float = 1
    fuehler_timeout_feuer: float = 120  # s, stumm bei Feuer
    fuehler_ausfall: float = 60         # s unavailable/unknown bis zur Störung
    feuer_twt_anstieg: float = 3        # K in 10 min
    # Brennstoff Briketts (nur Regelstand neu, nur in der App, ohne Live-Helfer). Herleitung in Issue #3.
    startfenster_brikett: float = 20            # min
    anheizen_timeout_brikett: float = 60        # min
    klappe_min_abbrand_brikett: float = 50      # %
    klappe_glut_brikett: float = 25             # %, auch Untergrenze im Brikett-Nachlauf
    glut_max_dauer_brikett: float = 420         # min
    abkuehlen_max_dauer_brikett: float = 600    # min
    brikett_nachlauf: float = 600               # min nach dem letzten Brikett-Feuer
    # Regelstrategie (nur Regelstand neu, nur in der App): 1 = klassisch (PI + D, Kp/Tn/Kd oben),
    # 2 = Regelstrategie 2.0 (PI mit Schwingungswächter und gerichtetem Vorhalt, eigene Werte).
    # Herleitung in kern/schwingung.py.
    regelstrategie: float = 1
    regler2_verstaerkung: float = 0.3           # %/K
    regler2_nachstellzeit: float = 2            # min
    regler2_vorhaltung_fallend: float = 0.5     # %·min/K, Abgas fällt unter dem Ziel weiter: Klappe auf
    regler2_vorhaltung_steigend: float = 0.25   # %·min/K, Abgas steigt über dem Ziel weiter: Klappe zu

    @classmethod
    def feldnamen(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls))

    @classmethod
    def helfer_feldnamen(cls) -> tuple[str, ...]:
        """Felder mit Live-Helfer input_number.kamin_abbrand_<feld>; die übrigen kennt nur die App."""
        return tuple(f for f in cls.feldnamen() if f not in NUR_APP_FELDER)


@dataclass
class Eingaben:
    """Messwerte und Bedienzustände eines Laufs."""
    jetzt: float                        # Unix-Zeit
    t: Optional[float]                  # Abgas, None = kein Zahlenwert (unavailable/unknown)
    t_alter_s: float = 0.0              # Sekunden seit dem letzten gemeldeten Abgaswert
    g: float = 0.0                      # Gradient K/min (HA-Helfer)
    g_kurz: Optional[float] = None      # Steigung K/min über 60 s (App), None = g verwenden
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
    brennstoff: str = HOLZ
    fuehler_defekt: bool = False        # Fehlermeldung des Abgasfühlers (Rolle abgas_fehler) ist an


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
    brennstoff: Optional[str] = None     # Wahl im letzten Lauf, um das Umstellen zu erkennen
    brikett_nachlauf_bis: Optional[float] = None  # bis dahin glüht womöglich noch Kohle (CO)
    # Regelstrategie 2.0: Dämpfung des Reglers (1 = voll) und der Schwingungswächter (kern/schwingung.py)
    daempfung: float = 1.0
    sw_extrem: Optional[float] = None       # laufendes Maximum (steigend) oder Minimum (fallend)
    sw_extrem_zeit: Optional[float] = None
    sw_gegen: Optional[float] = None        # vor der ersten Umkehr: das andere Extrem
    sw_gegen_zeit: Optional[float] = None
    sw_richtung: int = 0
    sw_beginn: Optional[float] = None       # Zeitpunkt des letzten Umkehrpunkts
    sw_wellen: tuple[float, ...] = ()
    sw_ruhig_seit: Optional[float] = None
    sw_gedaempft: Optional[float] = None    # Zeitpunkt der letzten Dämpfung


NUR_APP_FELDER = frozenset({
    "startfenster_brikett", "anheizen_timeout_brikett", "klappe_min_abbrand_brikett", "klappe_glut_brikett",
    "glut_max_dauer_brikett", "abkuehlen_max_dauer_brikett", "brikett_nachlauf",
    "regelstrategie", "regler2_verstaerkung", "regler2_nachstellzeit",
    "regler2_vorhaltung_fallend", "regler2_vorhaltung_steigend",
    "fuehler_art", "fuehler_timeout_feuer", "fuehler_ausfall",
})


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
