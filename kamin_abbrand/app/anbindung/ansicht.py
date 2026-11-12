"""Übersetzt den Stand der App in das, was Oberfläche und Integration zeigen."""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from anbindung.anlagenprofil import AMORTISATION, KOSTEN, fehlende_angaben
from anbindung.betrieb import offene_punkte
from anbindung.buchfuehrung import EINSTELLUNGEN, SALDO, VORGABEN
from anbindung.einstellungen import AUSGANG, HAND_DAUERN_MIN, ROLLEN
from kern import abbrand_dauer, wartung, wirtschaft
from kern.wirtschaft import leistung_nach_kw
from kern.autotune import Probe
from kern.typen import (ABBRAND, ABKUEHLEN, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT, NEU, PARITAET,
                        SIGNAL_FRISCH, SIGNAL_STUMM, STANDBY, STOERUNG, TUER_OFFEN, WARTUNG)

BEOBACHTUNG_TEXT = "Lauschen: Die App steuert die Klappe nicht. Das geht erst im Betrieb."
HAND_LAUSCHEN_TEXT = ("Handbetrieb der App geht nur im Betrieb. Im Lauschen stellt die Automation die Klappe, "
                      "dort den Handbetrieb in Home Assistant nutzen.")
BETRIEB_NUR_HA_TEXT = ("Auch im Betrieb liest die App das nur. Bitte die zugewiesene Entität "
                       "in Home Assistant schalten.")
AUTOTUNE_GESPERRT = "In dieser Version gesperrt: Der Testlauf darf die Klappe nicht bewegen."
QUITTIEREN_LAUSCHEN_TEXT = ("Im Lauschen quittiert die App nichts: Die Automation in Home Assistant führt die Phase. "
                            "Eine Störung endet dort, sobald der Fühler wieder Werte liefert, ein Anheizfehler nach 90 min.")

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
    """Im Lauschen bestimmt die Live-Automation, was der Ofen wirklich tut, im Betrieb die App."""
    if app.im_betrieb:
        stand = app.letzte.get(NEU)
        return (stand[0].zustand, "app") if stand else (None, "keine")
    live = app.eingaenge.startzustand(jetzt) if not app.ohne_helfer else None
    if live is not None:
        return live, "automation"
    stand = app.letzte.get(NEU) or app.letzte.get(PARITAET)
    return (stand[0].zustand, "app") if stand else (None, "keine")


def _rolle(app: Any, name: str) -> Optional[str]:
    return app.speicher.rollen.get(name)


def nachlegen_moeglich(app: Any) -> bool:
    """Der Puffer ist nicht voll (oder es gibt keinen), Nachlegen lohnt also."""
    puffer = app.eingaenge.zahl(_rolle(app, "puffer_unten"))
    return puffer is None or puffer < app.eingaenge.parameter.puffer_voll


def reinigung_zeigen(app: Any) -> Optional[dict]:
    """None, wenn es keine Erinnerung gibt (luftgeführt oder 0 h)."""
    if not app.speicher.anlagenprofil.wassergefuehrt:
        return None
    stand = app.speicher.wartungsstand
    r = wartung.pruefen(float(app.speicher.daten["betrieb_s"]), stand, app.speicher.reinigung_intervall_h)
    if not r.aktiv:
        return None
    return {"intervall_h": r.intervall_h, "gefahren_h": round(r.gefahren_h, 1), "rest_h": round(r.rest_h, 1),
            "faellig": r.faellig, "bald": r.bald, "letzte": stand.letzte_reinigung}


def wasser_zeigen(app: Any) -> Optional[dict]:
    """Karte der Wasserseite. None bei luftgeführtem Ofen. Leistung und Energie sind nur Anzeige."""
    if not app.speicher.anlagenprofil.wassergefuehrt:
        return None
    rollen, ein = app.speicher.rollen, app.eingaenge
    erg = app.wasser_ergebnis

    def lesen(rolle: str, umrechnen, einheiten: str) -> tuple[Optional[float], Optional[str]]:
        eid = rollen.get(rolle)
        eintrag = ein.eintrag(eid)
        if not eid or eintrag is None:
            return None, None
        einheit = eintrag.attribute.get("unit_of_measurement")
        zahl = ein.zahl(eid)
        if zahl is None:
            return None, None
        wert = umrechnen(zahl, einheit)
        return wert, None if wert is not None else f"Die Einheit „{einheit or 'fehlt'}“ wird nicht gelesen. Erlaubt: {einheiten}."

    leistung, leistung_problem = lesen("waermeleistung", leistung_nach_kw, "W, kW")
    energie_stand, energie_problem = lesen("waermezaehler", wirtschaft.energie_nach_kwh, "Wh, kWh, MWh")
    heute = None
    buch = getattr(app, "buchfuehrung", None)
    if buch is not None and energie_stand is not None:
        zeile = buch.tage.get(datetime.fromtimestamp(app._uhr()).date().isoformat())
        heute = zeile.get("waerme_zaehler_kwh") if zeile else None
    return {
        "stufe": erg.stufe if erg else "ok", "befunde": [b.als_dict() for b in erg.befunde] if erg else [],
        "leistung_kw": leistung, "leistung_problem": leistung_problem,
        "energie_heute_kwh": heute, "energie_problem": energie_problem,
        "hat_waermezaehler": bool(rollen.get("waermezaehler")), "hat_leistung": bool(rollen.get("waermeleistung")),
    }


def betrieb_zeigen(app: Any, jetzt: float) -> dict:
    """Die Oberfläche zeigt nur an, ob etwas erlaubt ist, entschieden wird auf dem Server."""
    s = app.speicher
    eintrag = s.bestaetigung.get("betrieb") if s.bestaetigt else None
    return {
        "offene_punkte": [p.als_dict() for p in offene_punkte(app, jetzt)],
        "bestaetigt": s.bestaetigt,
        "bestaetigt_am": eintrag["datum"] if eintrag else None,
        "bestaetigt_version": eintrag["version"] if eintrag else None,
        "watchdog_zugewiesen": bool(s.rollen.get("ausgang_heartbeat")),
        "ohne_watchdog_bestaetigt": s.ohne_watchdog_bestaetigt,
        "stoerung": app.ausgaenge.stoerung if app.im_betrieb else None,
        "anlaufschutz": app.anlaufschutz,
        "rueckfall": app.rueckfall,
    }


RECHENHILFE_TEXT = ("Rechenhilfe: Die Zahlen beruhen auf deinen Preisen, Heizwerten und Nutzungsgraden. "
                    "Sie sind keine Abrechnung und keine Beratung.")


TAGE_ANZEIGEN = 60


def amortisation_zeigen(app: Any) -> Optional[dict]:
    """Kurzfassung für Start, Sensor und Integration. None, solange es keine Buchführung gibt."""
    buch = getattr(app, "buchfuehrung", None)
    if buch is None:
        return None
    profil = app.speicher.anlagenprofil
    a = buch.amortisation(profil)
    return {k: a[k] for k in ("status", "art", "prozent", "netto_eur", "rest_eur", "prognose_jahre", "investition")} | {
        "waehrung": profil.waehrung}


def wirtschaft_zeigen(app: Any, menge_kwh: Optional[float] = None) -> dict:
    """Brennstoffvergleich für eine Menge Nutzwärme, dazu Amortisation und Tageswerte."""
    profil = app.speicher.anlagenprofil
    v = wirtschaft.vergleich(profil, wirtschaft.VORGABE_MENGE_KWH if menge_kwh is None else menge_kwh)
    erg = {
        "waehrung": profil.waehrung,
        "rechenhilfe": RECHENHILFE_TEXT,
        "vergleich": v.als_dict(),
        "hinweise": [h for h in fehlende_angaben(profil, app.speicher.rollen) if h["bereich"] == KOSTEN],
    }
    buch = getattr(app, "buchfuehrung", None)
    if buch is None:
        return erg
    zeilen = buch.zeilen(profil)
    heute = zeilen[-1] if zeilen and zeilen[-1]["tag"] == datetime.fromtimestamp(app._uhr()).date().isoformat() else None
    k = buch.konfiguration(profil)
    return erg | {
        "amortisation": buch.amortisation(profil),
        "amortisation_hinweise": [h for h in fehlende_angaben(profil, app.speicher.rollen) if h["bereich"] == "Amortisation"],
        "tage": [_tag_zeigen(z) for z in zeilen[-TAGE_ANZEIGEN:]][::-1],
        "heute_auflagen": heute["auflagen"] if heute else 0,
        "kg_je_auflage": k.kg_je_auflage,
        "einstellungen": {n: {"wert": buch.einstellungen.get(n), "vorgabe": VORGABEN.get(n), "minimum": m, "maximum": x, "titel": t}
                          for n, (m, x, t) in EINSTELLUNGEN.items()},
        "saldo": buch.saldo,
        "saldo_felder": {n: t for n, (_, _, t) in SALDO.items()},
        "referenz": buch.referenz_vorschlag(profil).als_dict(),
    }


def _tag_zeigen(z: dict) -> dict:
    t = wirtschaft.mittel_t_aussen(z)
    return {"tag": z["tag"], "auflagen": z["auflagen"], "holz_kg": z["holz_kg"], "holz_eur": z["holz_eur"],
            "kaminwaerme_kwh": z.get("kaminwaerme_kwh", 0.0), "waerme_art": z.get("waerme_art"),
            "verbrauch_kwh": z["verbrauch_kwh"], "t_aussen": t, "einsparung_kwh": z.get("einsparung_kwh", 0.0),
            "einsparung_eur": z.get("einsparung_eur"), "art": z.get("art"), "vorlaeufig": not z["final"],
            "ohne_menge": z["ohne_menge"]}


def hand_zeigen(app, jetzt: float) -> dict:
    """Handbetrieb der App für Karte und Integration."""
    sp = app.speicher
    aktiv = app.hand_aktiv(jetzt)
    ha_an = app.eingaenge.wert(_rolle(app, "handbetrieb")) == "on"
    return {
        "aktiv": aktiv,
        "ha_an": ha_an,
        "rest_s": sp.hand_rest_s(jetzt) if aktiv else None,
        "bis": sp.hand_bis if aktiv else None,
        "dauer_min": sp.hand_dauer_min,
        "dauern_min": list(HAND_DAUERN_MIN),
        "klappe": float(sp.parameter_neu.get("klappe_manuell", app.parameter_neu.klappe_manuell)),
        "moeglich": app.im_betrieb,
        "grund": None if app.im_betrieb else HAND_LAUSCHEN_TEXT,
        "motorisiert": bool(sp.anlagenprofil.klappe_motorisiert),
    }


def status(app: Any, jetzt: Optional[float] = None) -> dict:
    jetzt = time.time() if jetzt is None else jetzt
    ein = app.eingaenge
    z, quelle = _zustand_zum_zeigen(app, jetzt)
    t = ein.temperatur_c(_rolle(app, "abgas"))
    p = ein.parameter
    phase = z.phase if z else None
    stil, blinkt, satz, info = ZUSTAENDE.get(phase, ("standby", False, "Warte auf Daten aus Home Assistant.", ""))
    puffer = ein.zahl(_rolle(app, "puffer_unten"))
    nachlegen = phase == GLUT and nachlegen_moeglich(app)
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
    leistung_live = ein.wert(f"input_select.{ein.praefix}_leistungsmodus")
    if leistung_live in ("unknown", "unavailable"):
        leistung_live = None
    leistung_app = app.speicher.leistung
    einrichtung = [x for x in fehlende_angaben(app.speicher.anlagenprofil, app.speicher.rollen)
                   if x["bereich"] not in (KOSTEN, AMORTISATION)]
    gesendet = app.ausgaenge.gesendet if app.im_betrieb else None
    fehlend = [r.titel for r in ROLLEN if r.stufe == "pflicht" and r.art != AUSGANG and not _rolle(app, r.schluessel)]
    hand = hand_zeigen(app, jetzt)
    if hand["aktiv"] and phase:
        stil, satz = "wartung", f"Handbetrieb: Klappe {hand['klappe']:.0f} %, noch {hand['rest_s'] / 60:.0f} min."
    return {
        "zeit": jetzt,
        "phase": phase,
        "stil": stil,
        "blinkt": blinkt,
        "satz": satz if phase else ("Bitte zuerst die Abgastemperatur zuweisen." if fehlend else satz),
        "info": info,
        "quelle": quelle,
        "modus": app.modus,
        "beobachtung": not app.im_betrieb,
        "betrieb": betrieb_zeigen(app, jetzt),
        "phasenband": PHASENBAND,
        "fortschritt": fortschritt,
        "seit_s": max(0, jetzt - z.phase_seit) if z else None,
        "abgas": t,
        "ziel": app.speicher.parameter_neu.get("t_ziel", p.t_ziel),
        "klappe_soll": gesendet if gesendet is not None else (z.klappe_soll if z else None),
        "klappe_ist": app.klappe_ist_prozent(_rolle(app, "klappe_ist")),
        "zug": ein.zahl(_rolle(app, "druck")),
        "twt": ein.zahl(_rolle(app, "t_wt")),
        "puffer_unten": puffer,
        "gradient": app.letzte_eingaben.g if app.letzte_eingaben else None,
        "t_maximum": z.t_maximum if z else None,
        "phase_seit": z.phase_seit if z else None,
        "abbrand_dauer": _dauer_zeigen(app, jetzt, p.t_glut),
        "lueftung_frei": {"on": True, "off": False}.get(ein.wert(_rolle(app, "lueftung"))),
        "klappe_folgt_nicht": _klappe_folgt_nicht(app),
        "i_anteil": z.i_anteil if z else None,
        "tuer_offen": {"on": True, "off": False}.get(ein.wert(tuer_eid)),
        "tuerkontakt_vorhanden": app.tuerkontakt_vorhanden(),
        "feuer": phase in _FEUER_PHASEN,
        "stoerung": phase in (STOERUNG, ANHEIZFEHLER),
        "nachlegen_empfohlen": nachlegen,
        "fuehler_stumm": signal == SIGNAL_STUMM,
        "signal": signal or SIGNAL_FRISCH,
        "ausgaenge_zugewiesen": bool(_rolle(app, "ausgang_klappe")),
        "handbetrieb": ein.wert(_rolle(app, "handbetrieb")) == "on" or hand["aktiv"],
        "hand": hand,
        "wartung": ein.wert(_rolle(app, "wartung")) == "on",
        "klappe_manuell": p.klappe_manuell,
        "leistung": leistung_app or leistung_live,
        "leistung_live": leistung_live,
        "leistung_eigen": leistung_app is not None,
        "einrichtung": einrichtung,
        "betrieb_h": round(app.speicher.daten["betrieb_s"] / 3600.0, 1),
        "app": {
            "paritaet": _kurz(paritaet),
            "neu": _kurz(neu),
        },
        "fehlende_pflicht": fehlend,
        "reinigung": reinigung_zeigen(app),
        "amortisation": amortisation_zeigen(app),
        "wasser": wasser_zeigen(app),
        "fehler": dict(app._fehler),
    }


def _dauer_zeigen(app: Any, jetzt: float, t_glut: float) -> Optional[dict]:
    stand = getattr(app, "dauer", None)
    return None if stand is None else abbrand_dauer.anzeige(stand, jetzt) | {"t_glut": t_glut}


def _klappe_folgt_nicht(app: Any) -> bool:
    """Im Betrieb prüft die App selbst, im Lauschen meldet es die Live-Automation."""
    if app.im_betrieb:
        return bool(app.ausgaenge.stoerung_abweichung)
    return app.eingaenge.wert(f"input_boolean.{app.eingaenge.praefix}_klappe_stoerung") == "on"


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
    ergebnis: dict[str, list] = {"zeit": [], "t": [], "ziel": [], "twt": [], "klappe": [], "klappe_ist": [],
                                 "zug": [], "leistung": [], "phase": []}
    for zeile in zeilen[::schritt]:
        live = zeile.get("live") or {}
        lauf = (zeile.get("ergebnis") or {}).get("zustand") or {}
        anzeige = zeile.get("anzeige") or {}
        ergebnis["zeit"].append(zeile["eingaben"]["jetzt"])
        ergebnis["t"].append(zeile["eingaben"].get("t"))
        ergebnis["ziel"].append(anzeige.get("ziel"))
        ergebnis["twt"].append(zeile["eingaben"].get("twt_roh"))
        ergebnis["klappe"].append(live.get("klappe_soll", lauf.get("klappe_soll")))
        ergebnis["klappe_ist"].append(anzeige.get("klappe_ist"))
        ergebnis["zug"].append(anzeige.get("druck"))
        ergebnis["leistung"].append(anzeige.get("leistung_kw"))
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
