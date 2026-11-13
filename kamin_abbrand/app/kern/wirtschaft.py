"""Kosten und Amortisation. Reine Rechnung ohne Home Assistant.

Preise werden nie vorbelegt: Ein Brennstoff ohne Preis erscheint nicht im Vergleich, sondern
unter „fehlt“ mit dem Grund. Heizwerte und Nutzungsgrade haben änderbare Vorgaben.
Durch 0 wird nirgends geteilt; was sich nicht rechnen lässt, ist `None`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from anbindung.anlagenprofil import (HOLZ_ART, Anlagenprofil, Erzeuger, Holz, anteile, art_von, kwh_je_einheit,
                                     preis_je_kwh, wirksamer_nutzungsgrad)

HOLZ = "holz"
VORGABE_MENGE_KWH = 1000.0


@dataclass(frozen=True)
class Zeile:
    """Eine Zeile des Vergleichs. Alle Beträge in der Währung des Profils."""
    schluessel: str                 # „holz“ oder die Art des Erzeugers
    titel: str
    je_kwh: float                   # Kosten je kWh Nutzwärme
    kosten: float                   # für die angegebene Nutzwärmemenge
    differenz: Optional[float]      # kosten − Kosten von Holz; positiv: teurer als Holz. None ohne Holz
    kostenlos: bool = False

    def als_dict(self) -> dict:
        return {"schluessel": self.schluessel, "titel": self.titel, "je_kwh": self.je_kwh,
                "kosten": self.kosten, "differenz": self.differenz, "kostenlos": self.kostenlos}


@dataclass(frozen=True)
class Fehlt:
    schluessel: str
    titel: str
    grund: str

    def als_dict(self) -> dict:
        return {"schluessel": self.schluessel, "titel": self.titel, "grund": self.grund}


@dataclass(frozen=True)
class Vergleich:
    menge_kwh: float
    zeilen: tuple[Zeile, ...]       # günstigster zuerst
    fehlt: tuple[Fehlt, ...]

    def als_dict(self) -> dict:
        return {"menge_kwh": self.menge_kwh, "zeilen": [z.als_dict() for z in self.zeilen],
                "fehlt": [f.als_dict() for f in self.fehlt]}


def _positiv(wert: Optional[float]) -> bool:
    return wert is not None and wert > 0


def holz_nutzungsgrad(holz: Holz) -> float:
    """η des Ofens: eigener Wert oder Vorgabe."""
    return holz.nutzungsgrad_ofen if _positiv(holz.nutzungsgrad_ofen) else HOLZ_ART.nutzungsgrad


def holz_kosten_je_kwh(holz: Holz) -> tuple[Optional[float], Optional[str]]:
    """(€/kWh Nutzwärme, Grund) – genau eines von beiden ist None."""
    endenergie = preis_je_kwh(holz)
    if endenergie is None:
        return None, "Preis fehlt"
    return endenergie / holz_nutzungsgrad(holz), None


def erzeuger_kosten_je_kwh(erzeuger: Erzeuger) -> tuple[Optional[float], Optional[str]]:
    """Kostenlose Energie (Solarthermie) kostet 0, ohne Preis oder Nutzungsgrad gibt es keinen Wert."""
    endenergie = preis_je_kwh(erzeuger)
    if endenergie is None:
        return None, "Preis fehlt"
    if art_von(erzeuger).kostenlos:
        return 0.0, None
    grad = wirksamer_nutzungsgrad(erzeuger)
    if not _positiv(grad):
        return None, "Nutzungsgrad fehlt"
    return endenergie / grad, None


def vergleich(profil: Anlagenprofil, menge_kwh: float = VORGABE_MENGE_KWH) -> Vergleich:
    """Holz und jeder Erzeuger des Profils für `menge_kwh` Nutzwärme. Ohne gültige Menge gilt die Vorgabe."""
    menge = menge_kwh if _positiv(menge_kwh) else VORGABE_MENGE_KWH
    rohe: list[tuple[str, str, float, bool]] = []
    fehlt: list[Fehlt] = []
    je_kwh, grund = holz_kosten_je_kwh(profil.holz)
    if je_kwh is None:
        fehlt.append(Fehlt(HOLZ, HOLZ_ART.titel, grund))
    else:
        rohe.append((HOLZ, HOLZ_ART.titel, je_kwh, False))
    for erzeuger in profil.erzeuger:
        art = art_von(erzeuger)
        je_kwh, grund = erzeuger_kosten_je_kwh(erzeuger)
        if je_kwh is None:
            fehlt.append(Fehlt(art.schluessel, art.titel, grund))
        else:
            rohe.append((art.schluessel, art.titel, je_kwh, art.kostenlos))
    holz_kosten = next((j * menge for s, _, j, _ in rohe if s == HOLZ), None)
    zeilen = sorted(
        (Zeile(s, t, j, j * menge, None if holz_kosten is None else j * menge - holz_kosten, k) for s, t, j, k in rohe),
        key=lambda z: (z.je_kwh, z.titel))
    return Vergleich(menge, tuple(zeilen), tuple(fehlt))


# ---- Einheiten --------------------------------------------------------------

_ENERGIE_FAKTOR = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}
_LEISTUNG_FAKTOR = {"w": 0.001, "kw": 1.0}


def _faktor(tabelle: dict[str, float], einheit: object) -> Optional[float]:
    return tabelle.get(einheit.strip().casefold()) if isinstance(einheit, str) else None


def energie_nach_kwh(wert: Optional[float], einheit: object) -> Optional[float]:
    """Wh, kWh oder MWh in kWh. None bei unbekannter Einheit: Lieber nichts buchen als falsch."""
    faktor = _faktor(_ENERGIE_FAKTOR, einheit)
    return None if wert is None or faktor is None else wert * faktor


def leistung_nach_kw(wert: Optional[float], einheit: object) -> Optional[float]:
    """W oder kW in kW, sonst None."""
    faktor = _faktor(_LEISTUNG_FAKTOR, einheit)
    return None if wert is None or faktor is None else wert * faktor


# ---- Nutzwärme und Referenz ---------------------------------------------------

VORGABE_RAUM_WASSER = 0.7
VORGABE_AUFSTELLRAUM = 0.4
KAMINTAG_KWH = 1.0
ABDECKUNG_VOLLER_TAG_S = 20 * 3600.0
LERNEN_MIN_TAGE = 14
LERNEN_MIN_SPREIZUNG_K = 5.0
PROGNOSE_AB_KWH = 50.0
WAERME_ABDECKUNG = 0.9      # Anteil der erfassten Zeit, in dem der Wärmezähler lesbar sein muss


def nutzwaerme_mit_zaehler(qw_kwh: float, raum_wasser: float = VORGABE_RAUM_WASSER,
                           aufstellraum: float = VORGABE_AUFSTELLRAUM) -> float:
    """Wasserwärme plus die Wärme, die der Ofen in den Raum gibt: Qw + Qw × Verhältnis × Anteil."""
    return qw_kwh * (1.0 + raum_wasser * aufstellraum)


def nutzwaerme_aus_holz(kg: float, heizwert: float, nutzungsgrad: float) -> float:
    return kg * heizwert * nutzungsgrad


def referenz_kwh(a: Optional[float], b: Optional[float], sockel: Optional[float],
                 t_aussen: Optional[float]) -> Optional[float]:
    """Verbrauch der bisherigen Heizung an einem Tag ohne Feuer: max(a − b × T, Sockel)."""
    if a is None or b is None or t_aussen is None:
        return None
    return max(a - b * t_aussen, sockel or 0.0)


@dataclass(frozen=True)
class ReferenzVorschlag:
    ok: bool
    grund: str
    a: Optional[float] = None
    b: Optional[float] = None
    sockel: Optional[float] = None
    tage: int = 0

    def als_dict(self) -> dict:
        return {"ok": self.ok, "grund": self.grund, "a": self.a, "b": self.b, "sockel": self.sockel, "tage": self.tage}


def referenz_lernen(tage: list[tuple[float, float]]) -> ReferenzVorschlag:
    """Ausgleichsgerade Verbrauch = a − b × T aus Tagen ohne Feuer, jeder Eintrag (T_außen, kWh).

    Nur ein Vorschlag: mindestens 14 Tage und eine Spreizung der Außentemperatur von 5 K.
    """
    if len(tage) < LERNEN_MIN_TAGE:
        return ReferenzVorschlag(False, f"Es liegen {len(tage)} Tage ohne Feuer vor, nötig sind {LERNEN_MIN_TAGE}.", tage=len(tage))
    temperaturen = [t for t, _ in tage]
    spreizung = max(temperaturen) - min(temperaturen)
    if spreizung < LERNEN_MIN_SPREIZUNG_K:
        return ReferenzVorschlag(False, f"Die Außentemperatur schwankt nur um {spreizung:.1f} K, nötig sind {LERNEN_MIN_SPREIZUNG_K:g} K.",
                                 tage=len(tage))
    n = len(tage)
    mittel_t = sum(temperaturen) / n
    mittel_v = sum(v for _, v in tage) / n
    sxx = sum((t - mittel_t) ** 2 for t in temperaturen)
    steigung = sum((t - mittel_t) * (v - mittel_v) for t, v in tage) / sxx    # sxx > 0 wegen der Spreizung
    if steigung >= 0:
        return ReferenzVorschlag(False, "Der Verbrauch steigt nicht, wenn es kälter wird. Daraus lässt sich keine Referenz ableiten.", tage=n)
    a, b = mittel_v - steigung * mittel_t, -steigung
    waermste = sorted(tage)[-max(3, n // 3):]
    sockel = max(0.0, sorted(v for _, v in waermste)[len(waermste) // 2])
    return ReferenzVorschlag(True, f"Aus {n} Tagen ohne Feuer.", round(a, 2), round(b, 3), round(sockel, 2), n)


# ---- Ein Tag --------------------------------------------------------------------


@dataclass(frozen=True)
class Anteil:
    """Ein ersetzter Erzeuger: sein Anteil und was seine Wärme kostet."""
    anteil: float                       # Bruch, 0 bis 1
    je_kwh_nutzwaerme: Optional[float]  # None ohne Preis
    je_kwh_endenergie: Optional[float]
    nutzungsgrad: Optional[float]       # None bei Solarthermie


@dataclass(frozen=True)
class Konfiguration:
    """Alles, was zur Bewertung eines Tages gebraucht wird. Preise gelten ab dem Moment der Buchung."""
    kg_je_auflage: Optional[float]
    holz_heizwert: float
    holz_nutzungsgrad: float
    holz_eur_je_kg: Optional[float]
    raum_wasser: float = VORGABE_RAUM_WASSER
    aufstellraum: float = VORGABE_AUFSTELLRAUM
    anteile: tuple[Anteil, ...] = ()
    referenz_a: Optional[float] = None
    referenz_b: Optional[float] = None
    referenz_sockel: Optional[float] = None
    laufende_kosten_jahr: float = 0.0


def konfiguration_aus(profil: Anlagenprofil, einstellungen: dict) -> Konfiguration:
    """Setzt Profil und eigene Einstellungen zusammen. Fehlende Preise bleiben None."""
    holz = profil.holz
    heizwert = kwh_je_einheit(holz, "kg")
    holz_end = preis_je_kwh(holz)
    brueche = anteile(profil)
    liste = []
    for erzeuger, bruch in zip(profil.erzeuger, brueche or ()):
        if bruch <= 0:
            continue
        je_nutz, _ = erzeuger_kosten_je_kwh(erzeuger)
        liste.append(Anteil(bruch, je_nutz, preis_je_kwh(erzeuger),
                            None if art_von(erzeuger).kostenlos else wirksamer_nutzungsgrad(erzeuger)))
    return Konfiguration(
        kg_je_auflage=holz.kg_je_auflage if _positiv(holz.kg_je_auflage) else None,
        holz_heizwert=heizwert or HOLZ_ART.heizwert,
        holz_nutzungsgrad=holz_nutzungsgrad(holz),
        holz_eur_je_kg=None if holz_end is None or heizwert is None else holz_end * heizwert,
        raum_wasser=einstellungen.get("raum_wasser_verhaeltnis", VORGABE_RAUM_WASSER),
        aufstellraum=einstellungen.get("aufstellraum_anteil", VORGABE_AUFSTELLRAUM),
        anteile=tuple(liste),
        referenz_a=einstellungen.get("referenz_a"), referenz_b=einstellungen.get("referenz_b"),
        referenz_sockel=einstellungen.get("referenz_sockel"),
        laufende_kosten_jahr=einstellungen.get("laufende_kosten_jahr") or 0.0)


def zaehler_vollstaendig(zeile: dict) -> bool:
    """Ob der Wärmezähler den Tag ganz gesehen hat. Zeilen ohne die Angabe gelten als ganz erfasst."""
    if zeile.get("waerme_luecke"):
        return False
    if "waerme_s" not in zeile:
        return True
    return zeile["waerme_s"] >= WAERME_ABDECKUNG * zeile.get("s", 0.0)


def kaminwaerme(zeile: dict, k: Konfiguration) -> tuple[float, str]:
    """(kWh Nutzwärme des Tages, Art). Mit vollständigem Wärmezähler gemessen, sonst aus dem Holz geschätzt."""
    zaehler = zeile.get("waerme_zaehler_kwh")
    if zaehler is not None and zaehler_vollstaendig(zeile):
        return nutzwaerme_mit_zaehler(zaehler, k.raum_wasser, k.aufstellraum), "gemessen"
    return float(zeile.get("waerme_holz_kwh") or 0.0), "modell"


def mittel_t_aussen(zeile: dict) -> Optional[float]:
    return zeile["t_summe"] / zeile["t_s"] if zeile.get("t_s") else None


def tag_auswerten(zeile: dict, k: Konfiguration, endgueltig: bool) -> dict:
    """Abgeleitete Werte eines Tages: kaminwaerme_kwh, einsparung_kwh, einsparung_eur, art.

    Gemessen wird die Einsparung erst am fertigen Tag: Referenz minus Verbrauch der bisherigen Heizung.
    Heute gilt vorläufig das Modell. An Tagen ohne Feuer (Kaminwärme unter 1 kWh) gibt es keine Einsparung.
    """
    q, waerme_art = kaminwaerme(zeile, k)
    ergebnis: dict = {"kaminwaerme_kwh": q, "waerme_art": waerme_art, "einsparung_kwh": 0.0, "einsparung_eur": 0.0, "art": None}
    if q < KAMINTAG_KWH:
        return ergebnis
    t = mittel_t_aussen(zeile)
    ref = referenz_kwh(k.referenz_a, k.referenz_b, k.referenz_sockel, t)
    verbrauch = zeile.get("verbrauch_kwh")
    preise = [a.je_kwh_endenergie for a in k.anteile]
    if endgueltig and ref is not None and verbrauch is not None and zeile.get("s", 0.0) >= ABDECKUNG_VOLLER_TAG_S:
        kwh = ref - verbrauch
        ergebnis |= {"art": "gemessen", "einsparung_kwh": kwh,
                     "einsparung_eur": None if not k.anteile or None in preise else kwh * sum(a.anteil * a.je_kwh_endenergie for a in k.anteile)}
        return ergebnis
    kwh = sum(a.anteil * q / a.nutzungsgrad for a in k.anteile if _positiv(a.nutzungsgrad))
    eur = None if not k.anteile or any(a.je_kwh_nutzwaerme is None for a in k.anteile) \
        else sum(a.anteil * q * a.je_kwh_nutzwaerme for a in k.anteile)
    return ergebnis | {"art": "modell", "einsparung_kwh": kwh, "einsparung_eur": eur}


# ---- Amortisation -----------------------------------------------------------------

KEINE, FEHLT, WENIG, PROGNOSE, FERTIG = "keine_amortisation", "unbekannt", "zu_wenig_daten", "prognose", "amortisiert"


def amortisation(zeilen: list[dict], saldo: dict, investition: Optional[float], jahreswaerme_kwh: Optional[float],
                 laufende_kosten_jahr: float = 0.0) -> dict:
    """Netto, Rest, Prozent und Prognose.

    Netto ist alles, was der Ofen bisher gebracht hat: Einsparung minus Holz minus laufende Kosten, samt Eröffnungssaldo.
    Ein Urteil gibt es erst ab 50 kWh Kaminwärme. Ist das Netto dann nicht positiv, lautet es „keine Amortisation“.
    Die Prognose rechnet den Ertrag je kWh Kaminwärme (Einsparung minus Holz) auf die Jahreswärme hoch und zieht
    die laufenden Kosten für ein ganzes Jahr ab. Sie fallen nach Zeit an, nicht nach Wärme.
    """
    einsparung = (saldo.get("einsparung_eur") or 0.0) + sum(z.get("einsparung_eur") or 0.0 for z in zeilen)
    holz = (saldo.get("holz_eur") or 0.0) + sum(z.get("holz_eur") or 0.0 for z in zeilen)
    laufend = sum(z.get("laufend_eur") or 0.0 for z in zeilen)
    waerme = (saldo.get("kaminwaerme_kwh") or 0.0) + sum(z.get("kaminwaerme_kwh") or 0.0 for z in zeilen)
    netto = einsparung - holz - laufend
    kamintage = [z for z in zeilen if (z.get("kaminwaerme_kwh") or 0.0) >= KAMINTAG_KWH]
    gemessen = sum(1 for z in kamintage if z.get("art") == "gemessen")
    modell = len(kamintage) - gemessen
    art = "keine" if not kamintage else "gemessen" if modell == 0 else "schaetzung" if gemessen == 0 else "gemischt"
    unvollstaendig = sum(1 for z in kamintage if z.get("einsparung_eur") is None)
    erg: dict = {"einsparung_eur": einsparung, "holz_eur": holz, "laufend_eur": laufend, "netto_eur": netto,
                 "kaminwaerme_kwh": waerme, "investition": investition, "art": art, "tage_gemessen": gemessen,
                 "tage_modell": modell, "tage_unvollstaendig": unvollstaendig,
                 "rest_eur": None, "prozent": None, "prognose_jahre": None, "jahresertrag_eur": None, "status": FEHLT}
    if not _positiv(investition):
        return erg | {"hinweis": "Die Investition fehlt. Ohne sie lässt sich keine Amortisation rechnen."}
    rest = max(investition - netto, 0.0)
    erg |= {"rest_eur": rest, "prozent": min(max(netto / investition * 100.0, 0.0), 100.0)}
    if netto >= investition:
        return erg | {"status": FERTIG, "rest_eur": 0.0, "hinweis": "Die Investition ist eingespielt."}
    if waerme < PROGNOSE_AB_KWH:
        return erg | {"status": WENIG, "hinweis": f"Noch zu wenig Daten: Ein Urteil gibt es ab {PROGNOSE_AB_KWH:g} kWh Kaminwärme."}
    if netto <= 0:
        return erg | {"status": KEINE, "hinweis": "Bisher bringt der Ofen netto nichts ein. Bei diesen Werten gibt es keine Amortisation."}
    if not _positiv(jahreswaerme_kwh):
        return erg | {"status": WENIG, "hinweis": "Für die Prognose fehlt die erwartete Jahreswärme."}
    jahresertrag = (einsparung - holz) / waerme * jahreswaerme_kwh - (laufende_kosten_jahr or 0.0)
    erg |= {"jahresertrag_eur": jahresertrag}
    if jahresertrag <= 0:
        return erg | {"status": KEINE, "hinweis": "Die laufenden Kosten eines Jahres zehren den Ertrag auf. "
                                                  "Bei diesen Werten gibt es keine Amortisation."}
    return erg | {"status": PROGNOSE, "prognose_jahre": rest / jahresertrag}
