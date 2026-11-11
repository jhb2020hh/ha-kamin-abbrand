"""Was an dieser Anlage vorhanden ist und wie ihre Zähler und Preise zu lesen sind.

Reine Daten und Funktionen ohne Home Assistant. Preise werden nie vorbelegt: Ein Eintrag
ohne Preis bleibt leer und fällt aus dem Vergleich. Heizwerte und Nutzungsgrade haben
Vorgaben; ein leeres Feld heißt, dass die Vorgabe gilt.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional, Union

LUFT, WASSER = "luftgefuehrt", "wassergefuehrt"
OFENARTEN = (LUFT, WASSER)
KWH, MWH, M3, LITER, KG, TONNE, RM, SRM = "kWh", "MWh", "m³", "l", "kg", "t", "rm", "srm"
ENTITAET_MUSTER = re.compile(r"[a-z_]+\.[a-z0-9_]+")
ZAEHLER_DOMAINS = ("sensor", "input_number", "number")
WAEHRUNG_VORGABE = "€"
_WAEHRUNG_MAX = 5
_ANTEIL_TOLERANZ = 0.5      # Prozentpunkte, damit 33,3 + 33,3 + 33,3 durchgeht

_EINHEIT_SCHREIBWEISEN = {"kwh": KWH, "mwh": MWH, "m³": M3, "m3": M3, "m^3": M3, "l": LITER, "liter": LITER,
                          "kg": KG, "t": TONNE, "rm": RM, "srm": SRM}


@dataclass(frozen=True)
class Art:
    """Eigenschaften und Vorgaben einer Energieart."""
    schluessel: str
    titel: str
    preis_einheiten: tuple[str, ...]    # leer: kostenlos
    zaehler_einheiten: tuple[str, ...]
    nutzungsgrad: Optional[float] = None    # Vorgabe; None: gibt es hier nicht
    jaz: bool = False                       # Nutzungsgrad ist eine Jahresarbeitszahl
    heizwert: Optional[float] = None
    heizwert_je: Optional[str] = None       # m³, l oder kg
    zustandszahl: Optional[float] = None
    kg_je_srm: Optional[float] = None
    kg_je_rm: Optional[float] = None

    @property
    def kostenlos(self) -> bool:
        return not self.preis_einheiten


ARTEN: dict[str, Art] = {a.schluessel: a for a in (
    Art("erdgas", "Erdgas", (KWH, M3), (KWH, MWH, M3), nutzungsgrad=0.90,
        heizwert=10.5, heizwert_je=M3, zustandszahl=0.95),
    Art("heizoel", "Heizöl", (LITER, M3, KWH), (LITER, M3, KWH, MWH), nutzungsgrad=0.85,
        heizwert=10.0, heizwert_je=LITER),
    Art("pellets", "Pellets", (KG, TONNE, KWH), (KG, KWH, MWH), nutzungsgrad=0.85,
        heizwert=4.8, heizwert_je=KG),
    Art("hackschnitzel", "Hackschnitzel", (SRM, TONNE, KG, KWH), (KG, KWH, MWH), nutzungsgrad=0.80,
        heizwert=4.0, heizwert_je=KG, kg_je_srm=200.0),
    Art("waermepumpe", "Wärmepumpe", (KWH,), (KWH, MWH), nutzungsgrad=3.0, jaz=True),
    Art("strom", "Strom direkt", (KWH,), (KWH, MWH), nutzungsgrad=1.0),
    Art("fernwaerme", "Fernwärme", (KWH,), (KWH, MWH), nutzungsgrad=0.98),
    Art("solarthermie", "Solarthermie", (), (KWH, MWH)),
)}
HOLZ_ART = Art("holz", "Brennholz", (KG, TONNE, RM, SRM, KWH), (), nutzungsgrad=0.80, heizwert=4.0, heizwert_je=KG,
               kg_je_srm=280.0, kg_je_rm=400.0)


@dataclass(frozen=True)
class Erzeuger:
    art: str
    anteil: Optional[float] = None              # % der vom Ofen ersetzten Wärme
    preis: Optional[float] = None               # je preis_einheit
    preis_einheit: Optional[str] = None
    nutzungsgrad: Optional[float] = None        # 0,9 = 90 %, bei der Wärmepumpe die JAZ
    heizwert: Optional[float] = None            # kWh je m³, l oder kg; bei Gas der Brennwert
    zustandszahl: Optional[float] = None
    kg_je_srm: Optional[float] = None
    zaehler: Optional[str] = None
    zaehler_einheit: Optional[str] = None


@dataclass(frozen=True)
class Holz:
    preis: Optional[float] = None
    preis_einheit: Optional[str] = None
    heizwert: Optional[float] = None
    kg_je_rm: Optional[float] = None
    kg_je_srm: Optional[float] = None
    nutzungsgrad_ofen: Optional[float] = None   # η des Ofens, leer: Vorgabe der Art
    kg_je_auflage: Optional[float] = None       # Gewicht einer Auflage, keine Vorgabe


Brennstoff = Union[Erzeuger, Holz]


@dataclass(frozen=True)
class Anlagenprofil:
    ofen: str = LUFT
    puffer: bool = False
    pumpe_extern: bool = False
    klappe_motorisiert: bool = True
    klappe_invertiert: bool = False     # 100 % Soll heißt am Ausgang 0
    klappe_y0: float = 0.0              # Startspannung in V eines Antriebs mit z. B. 2-10-V-Eingang (nur Licht)
    rueck_zu: Optional[float] = None    # Rückmeldung bei Klappe zu, in der Einheit des Sensors; None: Vorgabe der Einheit
    rueck_auf: Optional[float] = None   # Rückmeldung bei Klappe auf
    tuerkontakt: bool = True
    waehrung: str = WAEHRUNG_VORGABE
    erzeuger: tuple[Erzeuger, ...] = ()
    holz: Holz = Holz()

    @property
    def wassergefuehrt(self) -> bool:
        return self.ofen == WASSER

    def als_dict(self) -> dict:
        daten = asdict(self)
        daten["erzeuger"] = list(daten["erzeuger"])
        return daten


class ProfilFehler(ValueError):
    """Die Eingabe passt nicht; der Text ist für die Oberfläche bestimmt."""


# ---- Prüfen --------------------------------------------------------------


def _zahl(roh: Any, titel: str, minimum: float, maximum: float = math.inf) -> Optional[float]:
    """Zahl im Bereich oder None für ein leeres Feld."""
    if roh is None or roh == "":
        return None
    if isinstance(roh, bool):
        raise ProfilFehler(f"{titel}: Bitte eine Zahl eingeben.")
    try:
        zahl = float(roh)
    except (TypeError, ValueError):
        raise ProfilFehler(f"{titel}: Bitte eine Zahl eingeben.") from None
    if not math.isfinite(zahl):
        raise ProfilFehler(f"{titel}: Bitte eine Zahl eingeben.")
    if zahl < minimum or zahl > maximum:
        grenze = f"mindestens {minimum:g}" if math.isinf(maximum) else f"{minimum:g} bis {maximum:g}"
        raise ProfilFehler(f"{titel}: Erlaubt ist {grenze}.")
    return zahl


def _ja(roh: Mapping, name: str, titel: str, vorgabe: bool) -> bool:
    wert = roh.get(name, vorgabe)
    if not isinstance(wert, bool):
        raise ProfilFehler(f"{titel}: Bitte ja oder nein angeben.")
    return wert


def einheit_normal(text: Any) -> Optional[str]:
    """Einheit in unserer Schreibweise, zum Beispiel m3 → m³. None, wenn unbekannt."""
    return _EINHEIT_SCHREIBWEISEN.get(text.strip().casefold()) if isinstance(text, str) else None


def _einheit(roh: Any, erlaubt: tuple[str, ...], titel: str) -> Optional[str]:
    if roh is None or roh == "":
        return None
    einheit = einheit_normal(roh)
    if einheit not in erlaubt:
        raise ProfilFehler(f"{titel}: Erlaubt sind {', '.join(erlaubt)}.")
    return einheit


def _preis(roh: Mapping, art: Art) -> tuple[Optional[float], Optional[str]]:
    """Ein Preis ohne Einheit gilt je erster Einheit der Art."""
    if art.kostenlos:
        return None, None
    preis = _zahl(roh.get("preis"), f"{art.titel}: Preis", 0.0)
    einheit = _einheit(roh.get("preis_einheit"), art.preis_einheiten, f"{art.titel}: Einheit des Preises")
    return preis, einheit or (art.preis_einheiten[0] if preis is not None else None)


def _erzeuger(roh: Any) -> Erzeuger:
    if not isinstance(roh, Mapping):
        raise ProfilFehler("Ein Wärmeerzeuger ist ungültig.")
    art = ARTEN.get(roh.get("art")) if isinstance(roh.get("art"), str) else None
    if art is None:
        raise ProfilFehler(f"Unbekannter Wärmeerzeuger: {roh.get('art')}")
    t = art.titel
    preis, preis_einheit = _preis(roh, art)
    nutzungsgrad = None
    if art.nutzungsgrad is not None:
        nutzungsgrad = (_zahl(roh.get("nutzungsgrad"), f"{t}: JAZ", 1.0, 10.0) if art.jaz
                        else _zahl(roh.get("nutzungsgrad"), f"{t}: Nutzungsgrad", 0.1, 1.2))
    zaehler = roh.get("zaehler") or None
    if zaehler is not None:
        if not isinstance(zaehler, str) or not ENTITAET_MUSTER.fullmatch(zaehler):
            raise ProfilFehler(f"{t}: „{zaehler}“ ist keine gültige Entität.")
        if zaehler.split(".", 1)[0] not in ZAEHLER_DOMAINS:
            raise ProfilFehler(f"{t}: Als Zähler erlaubt sind {', '.join(ZAEHLER_DOMAINS)}.")
    zaehler_einheit = _einheit(roh.get("zaehler_einheit"), art.zaehler_einheiten, f"{t}: Einheit des Zählers")
    if zaehler and not zaehler_einheit:
        raise ProfilFehler(f"{t}: Bitte die Einheit des Zählers wählen.")
    return Erzeuger(
        art=art.schluessel,
        anteil=_zahl(roh.get("anteil"), f"{t}: Anteil", 0.0, 100.0),
        preis=preis,
        preis_einheit=preis_einheit,
        nutzungsgrad=nutzungsgrad,
        heizwert=_zahl(roh.get("heizwert"), f"{t}: Heizwert", 0.1, 50.0) if art.heizwert else None,
        zustandszahl=_zahl(roh.get("zustandszahl"), f"{t}: Zustandszahl", 0.5, 1.2) if art.zustandszahl else None,
        kg_je_srm=_zahl(roh.get("kg_je_srm"), f"{t}: kg je Schüttraummeter", 50.0, 800.0) if art.kg_je_srm else None,
        zaehler=zaehler,
        zaehler_einheit=zaehler_einheit if zaehler else None,
    )


def _holz(roh: Any) -> Holz:
    if roh is None:
        return Holz()
    if not isinstance(roh, Mapping):
        raise ProfilFehler("Die Angaben zum Holz sind ungültig.")
    preis, einheit = _preis(roh, HOLZ_ART)
    t = HOLZ_ART.titel
    return Holz(
        preis=preis,
        preis_einheit=einheit,
        heizwert=_zahl(roh.get("heizwert"), f"{t}: Heizwert", 0.1, 50.0),
        kg_je_rm=_zahl(roh.get("kg_je_rm"), f"{t}: kg je Raummeter", 100.0, 1000.0),
        kg_je_srm=_zahl(roh.get("kg_je_srm"), f"{t}: kg je Schüttraummeter", 50.0, 800.0),
        nutzungsgrad_ofen=_zahl(roh.get("nutzungsgrad_ofen"), f"{t}: Nutzungsgrad des Ofens", 0.1, 1.0),
        kg_je_auflage=_zahl(roh.get("kg_je_auflage"), f"{t}: kg je Auflage", 0.1, 50.0),
    )


def _waehrung(roh: Any) -> str:
    if roh is None or roh == "":
        return WAEHRUNG_VORGABE
    if not isinstance(roh, str) or len(roh.strip()) > _WAEHRUNG_MAX or not roh.strip():
        raise ProfilFehler(f"Währungszeichen: Bitte höchstens {_WAEHRUNG_MAX} Zeichen.")
    return roh.strip()


def profil_pruefen(roh: Any) -> Anlagenprofil:
    """Baut das Profil aus einer Eingabe oder wirft ProfilFehler mit einem Text für die Oberfläche."""
    if not isinstance(roh, Mapping):
        raise ProfilFehler("Das Anlagenprofil ist ungültig.")
    ofen = roh.get("ofen")
    if ofen not in OFENARTEN:
        raise ProfilFehler("Bitte die Ofenart wählen: luftgeführt oder wassergeführt.")
    wasser = ofen == WASSER
    rueck_zu = _zahl(roh.get("rueck_zu"), "Rückmeldung bei zu", -RUECK_GRENZE, RUECK_GRENZE)
    rueck_auf = _zahl(roh.get("rueck_auf"), "Rückmeldung bei auf", -RUECK_GRENZE, RUECK_GRENZE)
    if rueck_zu is not None and rueck_auf is not None and abs(rueck_auf - rueck_zu) < RUECK_MIN_ABSTAND:
        raise ProfilFehler(f"Rückmeldung: Zu und auf müssen mindestens {RUECK_MIN_ABSTAND:g} auseinanderliegen.")
    liste = roh.get("erzeuger") or []
    if not isinstance(liste, (list, tuple)):
        raise ProfilFehler("Die Liste der Wärmeerzeuger ist ungültig.")
    return Anlagenprofil(
        ofen=ofen,
        # Ohne Wasserseite gibt es weder Puffer noch Pumpe des Ofens.
        puffer=wasser and _ja(roh, "puffer", "Puffer", False),
        pumpe_extern=wasser and _ja(roh, "pumpe_extern", "Pumpe", False),
        klappe_motorisiert=_ja(roh, "klappe_motorisiert", "Klappe", True),
        klappe_invertiert=_ja(roh, "klappe_invertiert", "Klappe invertiert", False),
        klappe_y0=_zahl(roh.get("klappe_y0"), "Startspannung", 0, 9.5) or 0.0,
        rueck_zu=rueck_zu,
        rueck_auf=rueck_auf,
        tuerkontakt=_ja(roh, "tuerkontakt", "Türkontakt", True),
        waehrung=_waehrung(roh.get("waehrung")),
        erzeuger=tuple(_erzeuger(e) for e in liste),
        holz=_holz(roh.get("holz")),
    )


def profil_aus_bestand(rollen: Mapping[str, str], tuerkontakt: bool = True) -> Anlagenprofil:
    """Erstes Profil einer Anlage, die schon eingerichtet ist, abgeleitet aus ihren Zuweisungen."""
    puffer = bool(rollen.get("puffer_unten") or rollen.get("puffer_oben"))
    wasser = puffer or any(rollen.get(r) for r in _NUR_WASSER)
    return Anlagenprofil(ofen=WASSER if wasser else LUFT, puffer=puffer, pumpe_extern=wasser,
                         tuerkontakt=bool(tuerkontakt))


# ---- Umrechnen -----------------------------------------------------------

# Rückmeldung der Klappe: Messwert bei zu und auf je Einheit des Sensors, wenn nichts eingetragen ist.
RUECK_VORGABEN: dict[str, tuple[float, float]] = {"%": (0.0, 100.0), "v": (2.0, 10.0), "ma": (4.0, 20.0)}
RUECK_GRENZE = 100000.0
RUECK_MIN_ABSTAND = 1.0


def rueck_vorgabe(einheit: Optional[str]) -> Optional[tuple[float, float]]:
    """Vorgabe für zu/auf nach Einheit. Ohne Einheit gilt Prozent, eine unbekannte Einheit hat keine."""
    schluessel = (einheit or "%").strip().casefold() or "%"
    return RUECK_VORGABEN.get(schluessel)


def rueck_kennlinie(profil: Anlagenprofil, einheit: Optional[str]) -> Optional[tuple[float, float]]:
    """Messwert bei zu und auf. Eigene Werte gehen vor, ein leeres Feld nimmt die Vorgabe der Einheit."""
    vorgabe = rueck_vorgabe(einheit)
    zu = profil.rueck_zu if profil.rueck_zu is not None else (vorgabe[0] if vorgabe else None)
    auf = profil.rueck_auf if profil.rueck_auf is not None else (vorgabe[1] if vorgabe else None)
    if zu is None or auf is None or abs(auf - zu) < RUECK_MIN_ABSTAND:
        return None
    return zu, auf


def rueckmeldung_prozent(roh: Optional[float], einheit: Optional[str], profil: Anlagenprofil) -> Optional[float]:
    """Klappenstellung in % aus der Rückmeldung, linear zwischen zu und auf, begrenzt auf 0–100.

    zu > auf ist eine invertierte Rückmeldung. None, wenn der Wert fehlt oder die Einheit unbekannt ist
    und keine eigenen Werte eingetragen sind.
    """
    kennlinie = rueck_kennlinie(profil, einheit)
    if roh is None or kennlinie is None:
        return None
    zu, auf = kennlinie
    return min(max((roh - zu) / (auf - zu) * 100.0, 0.0), 100.0)



def art_von(brennstoff: Brennstoff) -> Art:
    return HOLZ_ART if isinstance(brennstoff, Holz) else ARTEN[brennstoff.art]


def wirksamer_nutzungsgrad(erzeuger: Erzeuger) -> Optional[float]:
    """Eigener Wert oder Vorgabe. None bei Solarthermie, dort gibt es keinen."""
    return erzeuger.nutzungsgrad if erzeuger.nutzungsgrad is not None else art_von(erzeuger).nutzungsgrad


def _wirksam(brennstoff: Brennstoff, feld: str) -> Optional[float]:
    eigen = getattr(brennstoff, feld, None)
    return eigen if eigen is not None else getattr(art_von(brennstoff), feld)


def kwh_je_einheit(brennstoff: Brennstoff, einheit: Optional[str]) -> Optional[float]:
    """Energie einer Mengeneinheit. None, wenn die Einheit nicht zu diesem Brennstoff passt."""
    art = art_von(brennstoff)
    if einheit not in art.preis_einheiten + art.zaehler_einheiten:
        return None
    if einheit == KWH:
        return 1.0
    if einheit == MWH:
        return 1000.0
    heizwert = _wirksam(brennstoff, "heizwert")
    if art.heizwert_je == M3:
        return heizwert * _wirksam(brennstoff, "zustandszahl")
    if art.heizwert_je == LITER:
        return heizwert * (1000.0 if einheit == M3 else 1.0)
    if einheit == KG:
        return heizwert
    if einheit == TONNE:
        return heizwert * 1000.0
    return heizwert * _wirksam(brennstoff, "kg_je_rm" if einheit == RM else "kg_je_srm")


def zaehler_nach_kwh(erzeuger: Erzeuger, stand: Optional[float]) -> Optional[float]:
    """Zählerstand oder Verbrauch in kWh Endenergie. None, wenn das nicht bestimmbar ist."""
    faktor = kwh_je_einheit(erzeuger, erzeuger.zaehler_einheit)
    return None if stand is None or faktor is None else stand * faktor


def preis_je_kwh(brennstoff: Brennstoff) -> Optional[float]:
    """Preis je kWh Endenergie. None ohne Preis, 0 bei kostenloser Energie wie Solarthermie."""
    art = art_von(brennstoff)
    if art.kostenlos:
        return 0.0
    faktor = kwh_je_einheit(brennstoff, brennstoff.preis_einheit)
    return None if brennstoff.preis is None or faktor is None else brennstoff.preis / faktor


def ist_fahrenheit(einheit: Any) -> bool:
    return einheit in ("°F", "℉")


def temperatur_nach_c(wert: float, einheit: Any) -> float:
    """°F wird umgerechnet, jede andere Angabe bleibt unverändert."""
    return (wert - 32.0) * 5.0 / 9.0 if ist_fahrenheit(einheit) else wert


def anteile(profil: Anlagenprofil) -> Optional[tuple[float, ...]]:
    """Anteile als Bruch, in der Reihenfolge der Erzeuger, oder None, solange sie nicht aufgehen.

    Ein einzelner Erzeuger ohne Angabe trägt alles.
    """
    werte = [e.anteil for e in profil.erzeuger]
    if werte == [None]:
        return (1.0,)
    if not werte or any(w is None for w in werte):
        return None
    summe = sum(werte)
    if abs(summe - 100.0) > _ANTEIL_TOLERANZ:
        return None
    return tuple(w / summe for w in werte)


# ---- Was zum Profil passt ------------------------------------------------

_NUR_WASSER = ("t_wt", "t_wt_anstieg", "vorlauf", "ruecklauf", "waermezaehler", "waermeleistung")
_NUR_PUFFER = ("puffer_unten", "puffer_oben")
_NUR_KLAPPE = ("klappe_ist", "ausgang_klappe", "ausgang_heartbeat", "wartung", "handbetrieb", "konkurrierende_automation")


def rolle_passt(schluessel: str, profil: Anlagenprofil) -> bool:
    """Ob die Einrichtung diese Rolle bei diesem Profil anzeigt."""
    if schluessel in _NUR_WASSER:
        return profil.wassergefuehrt
    if schluessel in _NUR_PUFFER:
        return profil.puffer
    if schluessel == "pumpe":
        return profil.pumpe_extern
    if schluessel in _NUR_KLAPPE:
        return profil.klappe_motorisiert
    if schluessel == "tuer":
        return profil.tuerkontakt
    return True


def feld_passt(name: str, profil: Anlagenprofil) -> bool:
    """Ob die Seite Einstellungen diesen Parameter bei diesem Profil anzeigt."""
    if name in ("t_wasser_max", "klappe_wasser", "feuer_twt_anstieg", "wt_reinigung_h") or name.startswith("wasser_"):
        return profil.wassergefuehrt
    if name == "puffer_voll":
        return profil.puffer
    if name.startswith("klappe_"):
        return profil.klappe_motorisiert
    return True


def zaehler_entitaeten(profil: Anlagenprofil) -> list[str]:
    return [e.zaehler for e in profil.erzeuger if e.zaehler]


KOSTEN, AMORTISATION, WASSERSEITE, GRUNDLAGE = "Kosten", "Amortisation", "Wasser", "Grundlage"
_WASSER_ROLLEN_TEXTE = (
    ("t_wt", "Wärmetauscher ist nicht zugewiesen. Die App erkennt zu heißes Wasser am Ofen nicht."),
    ("vorlauf", "Vorlauf ist nicht zugewiesen. Die Prüfung auf zu hohen Vorlauf bleibt aus."),
    ("ruecklauf", "Rücklauf ist nicht zugewiesen. Die Prüfung auf zu kalten Rücklauf bleibt aus."),
    ("pumpe", "Pumpe ist nicht zugewiesen. Die App erkennt nicht, ob die Pumpe bei Feuer steht."),
    ("puffer_unten", "Puffer unten ist nicht zugewiesen. Die App weiß nicht, ob der Puffer voll ist."),
    ("waermezaehler", "Wärmemengenzähler Energie ist nicht zugewiesen. Die Nutzwärme wird aus dem Holz geschätzt."),
)


def fehlende_angaben(profil: Anlagenprofil, rollen: Mapping[str, str]) -> list[dict[str, str]]:
    """Was fehlt und was das bewirkt, passend zum Profil. Jeder Eintrag: bereich und text."""
    hinweise: list[dict[str, str]] = []

    def melden(bereich: str, text: str) -> None:
        hinweise.append({"bereich": bereich, "text": text})

    if not rollen.get("abgas"):
        melden(GRUNDLAGE, "Abgastemperatur ist nicht zugewiesen. Ohne sie kann die App nichts berechnen.")
    if not profil.erzeuger:
        melden(KOSTEN, "Kein Wärmeerzeuger eingetragen. Der Brennstoffvergleich braucht mindestens einen.")
    for erzeuger in profil.erzeuger:
        if preis_je_kwh(erzeuger) is None:
            melden(KOSTEN, f"{art_von(erzeuger).titel}: Preis fehlt. Dieser Erzeuger erscheint nicht im Vergleich.")
    nur_vergleich = bool(profil.erzeuger) and all(e.anteil == 0 for e in profil.erzeuger)
    if nur_vergleich:
        melden(AMORTISATION, "Alle Wärmeerzeuger haben den Anteil 0 und dienen nur dem Vergleich. "
                             "Für die Einsparung braucht mindestens einer einen Anteil.")
    elif profil.erzeuger and anteile(profil) is None:
        if any(e.anteil is None for e in profil.erzeuger):
            melden(KOSTEN, "Bei mehreren Wärmeerzeugern braucht jeder einen Anteil. Die Summe muss 100 % ergeben.")
        else:
            summe = sum(e.anteil for e in profil.erzeuger)
            melden(KOSTEN, f"Die Anteile ergeben {summe:g} %, es müssen 100 % sein.")
    if preis_je_kwh(profil.holz) is None:
        melden(KOSTEN, "Brennholz: Preis fehlt. Ohne ihn lassen sich die Kosten nicht vergleichen.")
    if not rollen.get("energiezaehler") and not zaehler_entitaeten(profil):
        # Ein Zähler am Erzeuger misst die bisherige Heizung genauso, die Rolle ist dann entbehrlich.
        melden(AMORTISATION, "Energiezähler ist nicht zugewiesen. Die Einsparung kann nur geschätzt werden.")
    if not rollen.get("t_aussen"):
        melden(AMORTISATION, "Außentemperatur ist nicht zugewiesen. Ohne sie gibt es keine gemessene Referenz.")
    for rolle, text in _WASSER_ROLLEN_TEXTE:
        if rolle_passt(rolle, profil) and not rollen.get(rolle):
            melden(WASSERSEITE, text)
    return hinweise


def katalog() -> dict:
    """Was die Oberfläche für ihre Auswahlfelder braucht."""
    def zeigen(art: Art) -> dict:
        return asdict(art) | {"kostenlos": art.kostenlos}
    return {"arten": [zeigen(a) for a in ARTEN.values()], "holz": zeigen(HOLZ_ART),
            "zaehler_domains": list(ZAEHLER_DOMAINS)}
