"""Übersetzt den Stand der App in das, was Oberfläche und Integration zeigen."""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from anbindung.einstellungen import AUSGANG, ROLLEN
from kern.autotune import Probe
from kern.typen import (ABBRAND, ABKUEHLEN, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT, NEU, PARITAET,
                        SIGNAL_FRISCH, SIGNAL_STUMM, STANDBY, STOERUNG, TUER_OFFEN, WARTUNG)

BEOBACHTUNG_TEXT = "Beobachtungsbetrieb: wird von der Automation gesteuert"
AUTOTUNE_GESPERRT = "Bis zur Umschaltung gesperrt: Im Beobachtungsbetrieb darf die App die Klappe nicht bewegen."

# Phase: (Stil, blinkt, Satz für den Nutzer, Erklärung hinter dem Info-Knopf)
ZUSTAENDE: dict[str, tuple[str, bool, str, str]] = {
    STANDBY: ("standby", False, "Der Ofen ist aus. Es gibt nichts zu tun.",
              "Die Klappe ist zu, damit keine Wärme durch den Schornstein entweicht."),
    TUER_OFFEN: ("tuer", False, "Die Tür ist offen. Die Klappe steht voll auf.",
                 "So zieht der Rauch sicher ab. Nach dem Schließen beginnt das Anheizen."),
    ANHEIZEN: ("anheizen", True, "Das Feuer kommt in Gang. Tür bitte geschlossen lassen.",
               "Die Klappe bleibt weit offen, bis die Abgastemperatur den Abbrand erreicht."),
    ABBRAND: ("abbrand", False, "Das Feuer brennt gut. Die Klappe regelt von selbst.",
              "Die Steuerung hält die Abgastemperatur am Ziel und gibt dem Holz so viel Luft wie nötig."),
    AUSBRAND: ("ausbrand", False, "Das Holz brennt aus. Gleich entsteht die Glut.",
               "Die Temperatur fällt. Die Klappe bleibt offen, damit alles sauber verbrennt."),
    GLUT: ("glut", False, "Nur noch Glut. Der Puffer ist fast voll, Nachlegen lohnt nicht.",
           "Mit wenig Luft hält die Glut lange."),
    ABKUEHLEN: ("abkuehlen", False, "Der Ofen kühlt ab.",
                "Die Klappe schließt langsam, damit keine Glut erstickt."),
    ANHEIZFEHLER: ("fehler", True, "Das Feuer ist nicht richtig angegangen. Bitte nachsehen.",
                   "Oft ist das Holz zu feucht oder zu dick. Die Klappe bleibt offen."),
    STOERUNG: ("fehler", True, "Störung: Der Fühler meldet nichts. Die Klappe steht voll auf.",
               "Ohne Messwert öffnet die Klappe zur Sicherheit. Die Störung endet bei frischen Werten."),
    WARTUNG: ("wartung", False, "Wartung läuft.", "Die Klappe steht für die Reinigung auf der gewählten Stellung."),
}
PHASENBAND = (STANDBY, ANHEIZEN, ABBRAND, AUSBRAND, GLUT, ABKUEHLEN)
NACHLEGEN_TEXT = "Glutphase: Jetzt kannst du nachlegen."
_FEUER_PHASEN = (ANHEIZEN, ABBRAND, AUSBRAND, GLUT)


def _zustand_zum_zeigen(app: Any, jetzt: float) -> tuple[Optional[Any], str]:
    """Im Beobachtungsbetrieb bestimmt die Live-Automation, was der Ofen wirklich tut."""
    live = app.eingaenge.startzustand(jetzt) if not app.ohne_helfer else None
    if live is not None:
        return live, "automation"
    stand = app.letzte.get(NEU) or app.letzte.get(PARITAET)
    return (stand[0].zustand, "app") if stand else (None, "keine")


def _rolle(app: Any, name: str) -> Optional[str]:
    return app.speicher.rollen.get(name)


def status(app: Any, jetzt: Optional[float] = None) -> dict:
    jetzt = time.time() if jetzt is None else jetzt
    ein = app.eingaenge
    z, quelle = _zustand_zum_zeigen(app, jetzt)
    t = ein.zahl(_rolle(app, "abgas"))
    p = ein.parameter
    phase = z.phase if z else None
    stil, blinkt, satz, info = ZUSTAENDE.get(phase, ("standby", False, "Warte auf Daten aus Home Assistant.", ""))
    puffer = ein.zahl(_rolle(app, "puffer_unten"))
    nachlegen = phase == GLUT and (puffer is None or puffer < p.puffer_voll)
    if nachlegen:
        stil, satz = "nachlegen", NACHLEGEN_TEXT
    neu = app.letzte.get(NEU)
    paritaet = app.letzte.get(PARITAET)
    signal = neu[0].signal if neu else None
    tuer_eid = _rolle(app, "tuer") if app.tuerkontakt_vorhanden() else None
    fortschritt = None
    if phase == ANHEIZEN and t is not None:
        anfang = min(z.t_phasenstart, t)
        spanne = max(p.t_abbrandstart - anfang, 1.0)
        fortschritt = round(min(max((t - anfang) / spanne, 0.0), 1.0) * 100)
    leistung = ein.wert(f"input_select.{ein.praefix}_leistungsmodus")
    fehlend = [r.titel for r in ROLLEN if r.stufe == "pflicht" and r.art != AUSGANG and not _rolle(app, r.schluessel)]
    return {
        "zeit": jetzt,
        "phase": phase,
        "stil": stil,
        "blinkt": blinkt,
        "satz": satz if phase else ("Bitte zuerst die Abgastemperatur zuweisen." if fehlend else satz),
        "info": info,
        "quelle": quelle,
        "beobachtung": True,
        "phasenband": PHASENBAND,
        "fortschritt": fortschritt,
        "seit_s": max(0, jetzt - z.phase_seit) if z else None,
        "abgas": t,
        "ziel": p.t_ziel,
        "klappe_soll": z.klappe_soll if z else None,
        "klappe_ist": ein.stellung(_rolle(app, "klappe_ist")),
        "zug": ein.zahl(_rolle(app, "druck")),
        "twt": ein.zahl(_rolle(app, "t_wt")),
        "puffer_unten": puffer,
        "gradient": app.letzte_eingaben.g if app.letzte_eingaben else None,
        "i_anteil": z.i_anteil if z else None,
        "tuer_offen": {"on": True, "off": False}.get(ein.wert(tuer_eid)),
        "tuerkontakt_vorhanden": app.tuerkontakt_vorhanden(),
        "feuer": phase in _FEUER_PHASEN,
        "stoerung": phase in (STOERUNG, ANHEIZFEHLER),
        "nachlegen_empfohlen": nachlegen,
        "fuehler_stumm": signal == SIGNAL_STUMM,
        "signal": signal or SIGNAL_FRISCH,
        "ausgaenge_zugewiesen": bool(_rolle(app, "ausgang_klappe")),
        "handbetrieb": ein.wert(_rolle(app, "handbetrieb")) == "on",
        "wartung": ein.wert(_rolle(app, "wartung")) == "on",
        "klappe_manuell": p.klappe_manuell,
        "leistung": leistung if leistung not in (None, "unknown", "unavailable") else None,
        "betrieb_h": round(app.speicher.daten["betrieb_s"] / 3600.0, 1),
        "app": {
            "paritaet": _kurz(paritaet),
            "neu": _kurz(neu),
        },
        "fehlende_pflicht": fehlend,
        "fehler": dict(app._fehler),
    }


def _kurz(stand: Any) -> Optional[dict]:
    if not stand:
        return None
    ergebnis = stand[0]
    return {"phase": ergebnis.zustand.phase, "klappe": ergebnis.klappe, "i_anteil": ergebnis.zustand.i_anteil}


def _dateien(ordner: Path, von: float, bis: float) -> list[Path]:
    tag, letzter = date.fromtimestamp(von), date.fromtimestamp(bis)
    pfade = []
    while tag <= letzter:
        pfad = ordner / f"verlauf-{tag.isoformat()}.jsonl"
        if pfad.exists():
            pfade.append(pfad)
        tag += timedelta(days=1)
    return pfade


def _zeilen(ordner: Path, von: float, bis: float):
    # Der Textvergleich spart das Parsen der Läufe des 5-s-Takts.
    for pfad in _dateien(ordner, von, bis):
        try:
            with open(pfad, encoding="utf-8") as datei:
                for text in datei:
                    if '"regelstand": "paritaet"' not in text[:40]:
                        continue
                    try:
                        zeile = json.loads(text)
                        jetzt = zeile["eingaben"]["jetzt"]
                    except (ValueError, KeyError, TypeError):
                        continue
                    if von <= jetzt <= bis:
                        yield zeile
        except OSError:
            continue


def verlauf_lesen(ordner: Path, stunden: float, jetzt: Optional[float] = None, max_punkte: int = 720) -> dict:
    """Spalten statt Objekte, die Grafik braucht nur Reihen."""
    jetzt = time.time() if jetzt is None else jetzt
    zeilen = list(_zeilen(ordner, jetzt - stunden * 3600.0, jetzt))
    schritt = max(1, len(zeilen) // max_punkte)
    ergebnis: dict[str, list] = {"zeit": [], "t": [], "klappe": [], "zug": [], "phase": []}
    for zeile in zeilen[::schritt]:
        live = zeile.get("live") or {}
        lauf = (zeile.get("ergebnis") or {}).get("zustand") or {}
        ergebnis["zeit"].append(zeile["eingaben"]["jetzt"])
        ergebnis["t"].append(zeile["eingaben"].get("t"))
        ergebnis["klappe"].append(live.get("klappe_soll", lauf.get("klappe_soll")))
        ergebnis["zug"].append((zeile.get("anzeige") or {}).get("druck"))
        ergebnis["phase"].append(live.get("phase", lauf.get("phase")))
    return ergebnis


def proben_lesen(ordner: Path, tage: float = 7.0, jetzt: Optional[float] = None) -> list[Probe]:
    """Nur die Stellung der echten Klappe zeigt, wie der Ofen antwortet, also `live`."""
    jetzt = time.time() if jetzt is None else jetzt
    proben = []
    for zeile in _zeilen(ordner, jetzt - tage * 86400.0, jetzt):
        live, ein = zeile.get("live"), zeile["eingaben"]
        if not live or ein.get("t") is None or ein.get("ausloeser") != "tick":
            continue
        proben.append(Probe(ein["jetzt"], live["klappe_soll"], ein["t"], live["phase"], bool(ein.get("tuer_offen"))))
    return proben


def aenderungen_lesen(ordner: Path, anzahl: int = 15) -> list[dict]:
    try:
        with open(ordner / "aenderungen.jsonl", encoding="utf-8") as datei:
            zeilen = datei.readlines()[-anzahl:]
    except OSError:
        return []
    ergebnis = []
    for text in reversed(zeilen):
        try:
            ergebnis.append(json.loads(text))
        except ValueError:
            continue
    return ergebnis


def uhrzeit(zeit: float) -> str:
    return datetime.fromtimestamp(zeit).strftime("%d.%m. %H:%M")
