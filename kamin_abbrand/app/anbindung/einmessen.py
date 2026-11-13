"""Einmessen: Ablage der Aufzeichnungen und ihre Auswertung für die Oberfläche.

Je Aufzeichnung zwei Dateien im Ordner `einmessen` neben einstellungen.json: <id>.json ist
der Kopf (atomar geschrieben), <id>.jsonl die Proben im 5-s-Takt. Gerechnet wird im Kern
(kern/einmessen.py), hier wird nur gelesen, geschrieben und übersetzt. Ein Plattenfehler wird
geloggt und stört nie die Regelung.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from anbindung.einstellungen import EINMESS_ID_MUSTER, EinstellungsFehler, atomar_schreiben
from kern.einmessen import (FERTIG, FEUER, KALT, KEIN_FEUER, NACHLEGEN, PUNKT_NAMEN, PUNKTARTEN, Ereignis, Probe,
                            Punkt, Zyklenzaehler, ereignisse_ermitteln, punkte_vorschlagen, werte_ableiten)
from kern.typen import Parameter

log = logging.getLogger(__name__)

ORDNER = "einmessen"
MAX_ZYKLEN = 5
HOECHSTDAUER_S = 8 * 3600.0
KEIN_FEUER_S = 3600.0
MAX_VERLAUF_PUNKTE = 1500
MAX_NOTIZ = 200
MAX_PUNKTE = 100
MAX_AUFZEICHNUNGEN = 50         # darüber fallen die ältesten nicht maßgeblichen weg
PUNKT_TOLERANZ_S = 5.0          # so weit vor Start oder nach Ende wird ein Punkt noch an den Rand gesetzt

# Ende-Gründe
ENDE_FERTIG, ENDE_ABGEBROCHEN, ENDE_KEIN_FEUER = "fertig", "abgebrochen", "kein_feuer"
ENDE_KALT, ENDE_HOECHSTDAUER, ENDE_LAUSCHEN, ENDE_NEUSTART = "kalt", "hoechstdauer", "lauschen", "neustart"
ENDE_FEHLER = "fehler"
ENDE_TEXTE = {
    ENDE_FERTIG: "Fertig",
    ENDE_ABGEBROCHEN: "Abgebrochen",
    ENDE_KEIN_FEUER: "Kein Feuer erkannt (60 min)",
    ENDE_KALT: "Ofen kalt vor dem letzten Nachlegen",
    ENDE_HOECHSTDAUER: "Höchstdauer 8 h erreicht",
    ENDE_LAUSCHEN: "App auf Lauschen gewechselt",
    ENDE_NEUSTART: "App neu gestartet",
    ENDE_FEHLER: "Fehler beim Aufzeichnen",
}
LAEUFT_TEXT = "Läuft"

PUNKT_HILFE = {
    "anheizen_max": "Der steile Anstieg beim Anheizen lässt nach. Hier würdest du die Luft zurücknehmen.",
    "abbrand_stabil": "Das Feuer brennt sicher, ab hier beginnt der Abbrand.",
    "bester_abbrand": "So soll der Abbrand laufen: Mitte des ruhigen Bereichs um das Maximum.",
    "glut": "Die Flammen sind weg, nur noch Glut. Hier wurde nachgelegt.",
    "abkuehlen": "Die Glut ist aus, der Ofen kühlt ab.",
    "zu_heiss": "Hier war es zu heiß. Setzt nur du, die App schlägt ihn nicht vor.",
}
PUNKT_FELDER = {
    "anheizen_max": ("t_kaltstart_ende",),
    "abbrand_stabil": ("t_abbrandstart",),
    "bester_abbrand": ("t_ziel", "t_ziel_eco", "t_ziel_normal", "t_ziel_power"),
    "glut": ("t_glut", "ausbrand_abfall"),
    "abkuehlen": ("t_abkuehlen",),
    "zu_heiss": ("t_uebertemperatur",),
}


def kennung_gueltig(kennung: Any) -> bool:
    return isinstance(kennung, str) and bool(EINMESS_ID_MUSTER.fullmatch(kennung))


def _zahl(wert: Any) -> Optional[float]:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
        return None
    return float(wert)


def ende_text(kopf: Mapping[str, Any]) -> str:
    return ENDE_TEXTE.get(kopf.get("ende_grund"), LAEUFT_TEXT if kopf.get("ende") is None else "Beendet")


def ereignis_text(ereignis: Ereignis, n: int) -> str:
    if ereignis.art == FEUER:
        return "Feuer erkannt"
    if ereignis.art == NACHLEGEN:
        return f"Nachlegen {ereignis.zyklus - 1} von {n} erkannt"
    if ereignis.art == FERTIG:
        return "Handregelung beendet, die Automatik übernimmt"
    if ereignis.art == KALT:
        return "Ofen kalt"
    if ereignis.art == KEIN_FEUER:
        return "Kein Feuer erkannt"
    return ereignis.art


def start_pruefen(zyklen: Any, push: Any, notiz: Any) -> tuple[int, bool, str]:
    """Angaben beim Start. Der Text des Fehlers ist für die Oberfläche bestimmt."""
    if isinstance(zyklen, bool) or not isinstance(zyklen, (int, float)) or zyklen != int(zyklen) \
            or not 1 <= zyklen <= MAX_ZYKLEN:
        raise EinstellungsFehler(f"Bitte 1 bis {MAX_ZYKLEN} Nachlegezyklen wählen.")
    if notiz is None:
        notiz = ""
    if not isinstance(notiz, str):
        raise EinstellungsFehler("Die Notiz muss ein Text sein.")
    notiz = notiz.strip()
    if len(notiz) > MAX_NOTIZ:
        raise EinstellungsFehler(f"Die Notiz darf höchstens {MAX_NOTIZ} Zeichen lang sein.")
    return int(zyklen), bool(push), notiz


# ---- Proben -----------------------------------------------------------------

def probe_aus(zeile: Mapping[str, Any]) -> Optional[Probe]:
    zeit = _zahl(zeile.get("zeit"))
    if zeit is None:
        return None
    tuer = zeile.get("tuer")
    return Probe(zeit, _zahl(zeile.get("t")), _zahl(zeile.get("g")), tuer if isinstance(tuer, bool) else None,
                 _zahl(zeile.get("klappe")), _zahl(zeile.get("twt")))


def proben_aus(zeilen: list[dict]) -> list[Probe]:
    return [p for p in map(probe_aus, zeilen) if p is not None]


def _punkte_aus(kopf: Mapping[str, Any]) -> list[Punkt]:
    return [Punkt(p["art"], float(p["zeit"]), int(p["zyklus"]), bool(p.get("auto", False)))
            for p in kopf.get("punkte") or []]


def _punkt_dict(p: Punkt) -> dict:
    return {"art": p.art, "zeit": p.zeit, "zyklus": p.zyklus, "auto": p.auto}


def _kopf_pruefen(roh: Any, kennung: str) -> dict:
    """Normalisiert einen gelesenen Kopf. ValueError, wenn er nicht zu gebrauchen ist."""
    if not isinstance(roh, dict) or roh.get("id") != kennung:
        raise ValueError("kein Kopf dieser Aufzeichnung")
    start = _zahl(roh.get("start"))
    n = roh.get("n")
    if start is None or isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_ZYKLEN:
        raise ValueError("Start oder Zyklen fehlen")
    punkte = []
    for p in roh.get("punkte") or []:
        if (isinstance(p, dict) and p.get("art") in PUNKTARTEN and _zahl(p.get("zeit")) is not None
                and isinstance(p.get("zyklus"), int) and not isinstance(p.get("zyklus"), bool) and p["zyklus"] >= 1):
            punkte.append({"art": p["art"], "zeit": float(p["zeit"]), "zyklus": p["zyklus"], "auto": bool(p.get("auto"))})
    nachgelegt = roh.get("nachgelegt")
    t_kalt = _zahl(roh.get("t_kalt"))
    return {
        "id": kennung, "start": start, "ende": _zahl(roh.get("ende")), "n": n,
        "nachgelegt": nachgelegt if isinstance(nachgelegt, int) and not isinstance(nachgelegt, bool) else 0,
        "notiz": roh.get("notiz") if isinstance(roh.get("notiz"), str) else "",
        "push": bool(roh.get("push")),
        "ende_grund": roh.get("ende_grund") if roh.get("ende_grund") in ENDE_TEXTE else None,
        "tuerkontakt": bool(roh.get("tuerkontakt")),
        "t_kalt": 50.0 if t_kalt is None else t_kalt,
        "handregelung_ende": _zahl(roh.get("handregelung_ende")),
        "punkte": punkte,
        "punkte_bearbeitet": bool(roh.get("punkte_bearbeitet")),
    }


# ---- Ablage ------------------------------------------------------------------

class Ablage:
    """Dateien der Aufzeichnungen. Jede Kennung wird gegen das Muster geprüft, kein Pfad-Ausbruch."""

    def __init__(self, ordner: Path) -> None:
        self.ordner = Path(ordner)

    def _pfad(self, kennung: str, endung: str) -> Path:
        if not kennung_gueltig(kennung):
            raise EinstellungsFehler("Diese Aufzeichnung gibt es nicht.")
        return self.ordner / f"{kennung}{endung}"

    def neue_kennung(self, start: float) -> str:
        basis = datetime.fromtimestamp(start).strftime("%Y-%m-%d_%H%M")
        kennung, zahl = basis, 1
        while self._pfad(kennung, ".json").exists() or self._pfad(kennung, ".jsonl").exists():
            zahl += 1
            kennung = f"{basis}-{zahl}"
        return kennung

    def kopf_schreiben(self, kopf: dict) -> bool:
        try:
            atomar_schreiben(self._pfad(kopf["id"], ".json"), kopf)
            return True
        except OSError as fehler:
            log.error("Kopf der Aufzeichnung %s nicht gespeichert: %r", kopf["id"], fehler)
            return False

    def kopf(self, kennung: str) -> Optional[dict]:
        """None, wenn es die Aufzeichnung nicht gibt oder der Kopf nicht lesbar ist."""
        pfad = self._pfad(kennung, ".json")
        try:
            with open(pfad, encoding="utf-8") as datei:
                return _kopf_pruefen(json.load(datei), kennung)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as fehler:
            log.warning("Aufzeichnung %s nicht lesbar: %r", kennung, fehler)
            return None

    def liste(self) -> list[dict]:
        """Alle lesbaren Köpfe, neueste zuerst."""
        try:
            pfade = list(self.ordner.glob("*.json"))
        except OSError:
            return []
        koepfe = [k for p in pfade if kennung_gueltig(p.stem) and (k := self.kopf(p.stem)) is not None]
        return sorted(koepfe, key=lambda k: (k["start"], k["id"]), reverse=True)

    def anhaengen(self, kennung: str, zeile: dict) -> None:
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            with open(self._pfad(kennung, ".jsonl"), "a", encoding="utf-8") as datei:
                datei.write(json.dumps(zeile, ensure_ascii=False) + "\n")
        except OSError as fehler:
            log.error("Probe der Aufzeichnung %s nicht gespeichert: %r", kennung, fehler)

    def proben(self, kennung: str) -> list[dict]:
        """Lesbare Zeilen nach Zeit. Eine halb geschriebene oder kaputte Zeile fällt weg."""
        zeilen = []
        try:
            with open(self._pfad(kennung, ".jsonl"), encoding="utf-8") as datei:
                for text in datei:
                    try:
                        zeile = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(zeile, dict) and _zahl(zeile.get("zeit")) is not None:
                        zeilen.append(zeile)
        except FileNotFoundError:
            return []
        except OSError as fehler:
            log.warning("Proben der Aufzeichnung %s nicht lesbar: %r", kennung, fehler)
        return sorted(zeilen, key=lambda z: z["zeit"])

    def loeschen(self, kennung: str) -> bool:
        gefunden = False
        for endung in (".json", ".jsonl"):
            pfad = self._pfad(kennung, endung)
            try:
                pfad.unlink()
                gefunden = True
            except FileNotFoundError:
                continue
            except OSError as fehler:
                log.error("%s nicht gelöscht: %r", pfad.name, fehler)
                raise EinstellungsFehler("Die Aufzeichnung konnte nicht gelöscht werden.") from fehler
        return gefunden

    def abschliessen(self, kopf: dict, ende: float, grund: str) -> tuple[dict, bool]:
        """Ende eintragen und Punkte automatisch vorschlagen. Zweiter Wert: Kopf geschrieben."""
        kopf = dict(kopf, ende=ende, ende_grund=grund)
        try:
            proben = proben_aus(self.proben(kopf["id"]))
            ereignisse = ereignisse_ermitteln(proben, kopf["n"], kopf["tuerkontakt"], kopf["t_kalt"], KEIN_FEUER_S)
            kopf["punkte"] = [_punkt_dict(p) for p in punkte_vorschlagen(proben, ereignisse, kopf["t_kalt"])]
        except Exception:
            log.exception("Punkte der Aufzeichnung %s nicht vorgeschlagen", kopf["id"])
            kopf["punkte"] = []
        kopf["punkte_bearbeitet"] = False
        return kopf, self.kopf_schreiben(kopf)

    def aufraeumen(self, schuetzen: set, behalten: int = MAX_AUFZEICHNUNGEN) -> list[str]:
        """Höchstens `behalten` Aufzeichnungen. Die ältesten gehen zuerst, nie eine geschützte."""
        koepfe = self.liste()
        weg = []
        for kopf in reversed(koepfe):
            if len(koepfe) - len(weg) <= behalten:
                break
            if kopf["id"] in schuetzen:
                continue
            try:
                self.loeschen(kopf["id"])
                weg.append(kopf["id"])
                log.info("Aufzeichnung %s gelöscht, es sind mehr als %d", kopf["id"], behalten)
            except EinstellungsFehler:
                break
        return weg

    def verwaiste_beenden(self) -> list[str]:
        """Nach einem Neustart: Was noch als laufend in der Datei steht, ist beendet."""
        beendet = []
        for kopf in self.liste():
            if kopf["ende"] is not None:
                continue
            zeilen = self.proben(kopf["id"])
            ende = zeilen[-1]["zeit"] if zeilen else kopf["start"]
            self.abschliessen(kopf, float(ende), ENDE_NEUSTART)
            log.warning("Aufzeichnung %s nach dem Neustart beendet", kopf["id"])
            beendet.append(kopf["id"])
        return beendet

    def punkte_setzen(self, kennung: str, roh: Any) -> dict:
        """Punkte des Nutzers. Ein unveränderter Vorschlag bleibt als automatisch gekennzeichnet."""
        kopf = self._fertiger_kopf(kennung)
        if not isinstance(roh, list) or len(roh) > MAX_PUNKTE:
            raise EinstellungsFehler("Die Punkte sind ungültig.")
        auto = {(p["art"], p["zeit"], p["zyklus"]) for p in kopf["punkte"] if p["auto"]}
        neu = []
        for p in roh:
            if not isinstance(p, dict) or p.get("art") not in PUNKTARTEN:
                raise EinstellungsFehler("Unbekannte Art von Punkt.")
            zeit, zyklus = _zahl(p.get("zeit")), p.get("zyklus")
            if (zeit is None or not kopf["start"] - PUNKT_TOLERANZ_S <= zeit <= kopf["ende"] + PUNKT_TOLERANZ_S):
                raise EinstellungsFehler(f"{PUNKT_NAMEN[p['art']]}: Der Punkt liegt außerhalb der Aufzeichnung.")
            zeit = min(max(zeit, kopf["start"]), kopf["ende"])
            if isinstance(zyklus, bool) or not isinstance(zyklus, int) or zyklus < 1:
                raise EinstellungsFehler(f"{PUNKT_NAMEN[p['art']]}: Der Zyklus muss 1 oder mehr sein.")
            neu.append({"art": p["art"], "zeit": zeit, "zyklus": zyklus, "auto": (p["art"], zeit, zyklus) in auto})
        kopf |= {"punkte": sorted(neu, key=lambda p: p["zeit"]), "punkte_bearbeitet": True}
        if not self.kopf_schreiben(kopf):
            raise EinstellungsFehler("Die Punkte konnten nicht gespeichert werden.")
        return kopf

    def punkte_zuruecksetzen(self, kennung: str) -> dict:
        kopf = self._fertiger_kopf(kennung)
        kopf, geschrieben = self.abschliessen(kopf, kopf["ende"], kopf["ende_grund"] or ENDE_FEHLER)
        if not geschrieben:
            raise EinstellungsFehler("Die Punkte konnten nicht gespeichert werden.")
        return kopf

    def _fertiger_kopf(self, kennung: str) -> dict:
        kopf = self.kopf(kennung)
        if kopf is None:
            raise EinstellungsFehler("Diese Aufzeichnung gibt es nicht.")
        if kopf["ende"] is None:
            raise EinstellungsFehler("Die Aufzeichnung läuft noch. Das geht erst nach ihrem Ende.")
        return kopf


# ---- Laufende Aufzeichnung ----------------------------------------------------

class Aufzeichnung:
    """Die laufende Aufzeichnung: Kopf, Zyklenzähler und Ablage. Je TICK eine Probe."""

    def __init__(self, ablage: Ablage, kopf: dict) -> None:
        self.ablage = ablage
        self.kopf = kopf
        self.zaehler = Zyklenzaehler(kopf["n"], kopf["tuerkontakt"], kopf["t_kalt"], KEIN_FEUER_S)

    @classmethod
    def starten(cls, ablage: Ablage, start: float, n: int, notiz: str, push: bool,
                tuerkontakt: bool, t_kalt: float) -> "Aufzeichnung":
        kopf = {"id": ablage.neue_kennung(start), "start": start, "ende": None, "n": n, "nachgelegt": 0,
                "notiz": notiz, "push": push, "ende_grund": None, "tuerkontakt": tuerkontakt, "t_kalt": t_kalt,
                "handregelung_ende": None, "punkte": [], "punkte_bearbeitet": False}
        if not ablage.kopf_schreiben(kopf):
            raise EinstellungsFehler("Die Aufzeichnung konnte nicht angelegt werden.")
        return cls(ablage, kopf)

    @property
    def kennung(self) -> str:
        return self.kopf["id"]

    @property
    def handregelung(self) -> bool:
        """Bis zum Ende des N-ten Nachlegens hält die App die Klappe auf 100 %."""
        return not self.zaehler.fertig

    @property
    def nachlauf(self) -> bool:
        return self.zaehler.fertig

    def schritt(self, zeile: dict) -> Optional[Ereignis]:
        self.ablage.anhaengen(self.kennung, zeile)
        probe = probe_aus(zeile)
        ereignis = self.zaehler.schritt(probe) if probe is not None else None
        if ereignis is not None:
            self.kopf["nachgelegt"] = self.zaehler.nachgelegt
            if ereignis.art == FERTIG:
                self.kopf["handregelung_ende"] = ereignis.zeit
            self.ablage.kopf_schreiben(self.kopf)
        return ereignis

    def beenden(self, ende: float, grund: str) -> tuple[dict, bool]:
        """Schreibt das Ende mit dem Grund, wie er ist (siehe grund_fuer). Zweiter Wert: geschrieben."""
        self.kopf, geschrieben = self.ablage.abschliessen(self.kopf, ende, grund)
        return self.kopf, geschrieben

    def grund_fuer(self, grund: str) -> str:
        """Nach der Handregelung ist die Aufzeichnung fertig, egal was den Nachlauf beendet."""
        return ENDE_FERTIG if self.nachlauf else grund

    def kurz(self, jetzt: float) -> dict:
        return {"id": self.kennung, "zyklus": self.zaehler.zyklus, "n": self.kopf["n"],
                "nachgelegt": self.zaehler.nachgelegt, "handregelung": self.handregelung,
                "nachlauf": self.nachlauf, "seit_s": max(0.0, jetzt - self.kopf["start"])}

    def zeigen(self, jetzt: float) -> dict:
        return self.kurz(jetzt) | {"start": self.kopf["start"], "push": self.kopf["push"], "notiz": self.kopf["notiz"]}


# ---- Für die Oberfläche --------------------------------------------------------

def kopf_zeigen(kopf: Mapping[str, Any], massgeblich: Optional[str], laufend: frozenset = frozenset()) -> dict:
    """laufend: Kennungen, die gerade laufen oder abgeschlossen werden. Eine andere ohne Ende
    (zum Beispiel nach einem Plattenfehler) gilt als mit Fehler beendet."""
    kopf = dict(kopf)
    if kopf["ende"] is None and kopf["id"] not in laufend:
        kopf["ende_grund"] = ENDE_FEHLER
    return {"id": kopf["id"], "start": kopf["start"], "ende": kopf["ende"], "n": kopf["n"],
            "nachgelegt": kopf["nachgelegt"], "notiz": kopf["notiz"], "ende_grund": kopf["ende_grund"],
            "ende_text": ende_text(kopf), "massgeblich": kopf["id"] == massgeblich,
            "punkte_bearbeitet": kopf["punkte_bearbeitet"]}


def verlauf_zeigen(zeilen: list[dict], max_punkte: int = MAX_VERLAUF_PUNKTE) -> dict:
    """Ausgedünnt auf etwa max_punkte. Die letzte Probe und jede Türflanke bleiben immer drin."""
    schritt = max(1, math.ceil(len(zeilen) / max_punkte))
    tueren = [z.get("tuer") if isinstance(z.get("tuer"), bool) else None for z in zeilen]
    auswahl = set(range(0, len(zeilen), schritt)) | ({len(zeilen) - 1} if zeilen else set())
    auswahl |= {i for i in range(1, len(zeilen)) if tueren[i] != tueren[i - 1]}
    verlauf: dict[str, list] = {"zeit": [], "t": [], "klappe": [], "twt": [], "pumpe": [], "tuer": [], "phase": []}
    for z in (zeilen[i] for i in sorted(auswahl)):
        verlauf["zeit"].append(z["zeit"])
        for feld in ("t", "klappe", "twt", "pumpe"):
            verlauf[feld].append(_zahl(z.get(feld)))
        tuer = z.get("tuer")
        verlauf["tuer"].append(int(tuer) if isinstance(tuer, bool) else None)
        verlauf["phase"].append(z.get("phase") if isinstance(z.get("phase"), str) else None)
    return verlauf


def auswerten(ablage: Ablage, kennung: str, parameter: Parameter,
              grenzen: Mapping[str, tuple[float, float, float]], ziele_alt: Mapping[str, float],
              felder: Mapping[str, Any]) -> Optional[dict]:
    """Verlauf, Ereignisse, Punkte und Vorschläge einer Aufzeichnung. None, wenn es sie nicht gibt.

    Solange sie läuft, sind die Punkte ein Vorschlag auf dem bisherigen Stand und nicht gespeichert."""
    kopf = ablage.kopf(kennung)
    if kopf is None:
        return None
    zeilen = ablage.proben(kennung)
    proben = proben_aus(zeilen)
    ereignisse = ereignisse_ermitteln(proben, kopf["n"], kopf["tuerkontakt"], kopf["t_kalt"], KEIN_FEUER_S)
    if kopf["ende"] is None:
        punkte = punkte_vorschlagen(proben, ereignisse, kopf["t_kalt"])
    else:
        punkte = _punkte_aus(kopf)
    vorschlaege = werte_ableiten(proben, punkte, parameter, grenzen, ziele_alt, ereignisse)
    return {
        "kopf": kopf,
        "verlauf": verlauf_zeigen(zeilen),
        "ereignisse": [{"art": e.art, "zeit": e.zeit, "zyklus": e.zyklus, "text": ereignis_text(e, kopf["n"])}
                       for e in ereignisse],
        "punkte": [_punkt_dict(p) for p in punkte],
        "punktarten": [{"art": art, "name": PUNKT_NAMEN[art], "hilfe": PUNKT_HILFE[art],
                        "felder": [f for f in PUNKT_FELDER[art] if f in felder],
                        "feld_titel": [felder[f].titel for f in PUNKT_FELDER[art] if f in felder]}
                       for art in PUNKTARTEN],
        "vorschlaege": [{"feld": v.feld, "titel": felder[v.feld].titel if v.feld in felder else v.feld,
                         "einheit": felder[v.feld].einheit if v.feld in felder else "", "wert": v.wert, "alt": v.alt,
                         "begruendung": v.begruendung, "punkte": v.punkte, "gleich": math.isclose(v.wert, v.alt)}
                        for v in vorschlaege],
    }
