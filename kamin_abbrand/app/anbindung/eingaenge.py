"""Spiegel der relevanten HA-Zustände und Übersetzung in die Typen des Kerns."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from anbindung.ableitung import Ableitung
from anbindung.anlagenprofil import Anlagenprofil, rueckmeldung_prozent, temperatur_nach_c
from kern.typen import (
    BEDIENUNG,
    PHASEN,
    STANDBY,
    TUER,
    WARTUNG_AN,
    WARTUNG_AUS,
    Eingaben,
    Parameter,
    Zustand,
)

log = logging.getLogger(__name__)

SENSOR_WEG_NACH_S = 60.0
_WEG_ZUSTAENDE = ("unavailable", "unknown")


@dataclass(frozen=True)
class Entitaeten:
    """Gelesene Entitäten. None heißt: nicht zugewiesen."""
    abgas: Optional[str] = None
    druck: Optional[str] = None
    gradient: Optional[str] = None
    t_max_3min: Optional[str] = None
    tuer: Optional[str] = None
    t_wt: Optional[str] = None
    t_wt_anstieg: Optional[str] = None
    puffer_unten: Optional[str] = None
    wartung: Optional[str] = None
    handbetrieb: Optional[str] = None
    tuerkontakt_vorhanden: Optional[str] = None
    can_alter: Optional[str] = None


@dataclass
class Eintrag:
    zustand: str
    attribute: dict
    gemeldet: float     # letzte Meldung, auch ohne Wertänderung (last_reported)
    geaendert: float    # letzte Änderung des Zustands (last_changed)


def als_zahl(wert: object) -> Optional[float]:
    """Zahl oder None, wie is_number in HA: Text, nan und inf zählen nicht."""
    try:
        zahl = float(wert)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return zahl if math.isfinite(zahl) else None


def _zeitstempel(text: object) -> Optional[float]:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


class Eingaenge:
    def __init__(self, entitaeten: Entitaeten, praefix: str, zusatz: Iterable[str] = ()) -> None:
        self.ent = entitaeten
        self.praefix = praefix
        self.ableitung = Ableitung()
        # Dieses Objekt bekommt auch der Kern: Geänderte Helfer wirken sofort.
        self.parameter = Parameter()

        self._parameter_ids = {
            f"input_number.{praefix}_{feld}": feld for feld in Parameter.feldnamen()
        }
        self._phase_id = f"input_select.{praefix}_phase"
        self._phase_seit_id = f"input_datetime.{praefix}_phase_seit"
        self._relevant_setzen(zusatz)

        self._spiegel: dict[str, Eintrag] = {}
        self._parameter_gewarnt: dict[str, str] = {}
        self._weg_seit: Optional[float] = None
        self._weg_gemeldet = False
        self._druck_wert: Optional[float] = None
        self._druck_zeit: Optional[float] = None

    def _relevant_setzen(self, zusatz: Iterable[str]) -> None:
        ent, praefix = self.ent, self.praefix
        self._bedienung_ids = {
            eid for eid in (
                ent.handbetrieb,
                f"input_number.{praefix}_klappe_manuell",
                f"input_number.{praefix}_klappe_wartung",
            ) if eid
        }
        self._relevant = {
            eid for eid in (
                ent.abgas, ent.druck, ent.gradient, ent.t_max_3min, ent.tuer, ent.t_wt,
                ent.t_wt_anstieg, ent.puffer_unten, ent.wartung, ent.handbetrieb,
                ent.tuerkontakt_vorhanden, ent.can_alter,
                self._phase_id, self._phase_seit_id,
                f"input_number.{praefix}_t_maximum",
                f"input_number.{praefix}_t_phasenstart",
                f"input_number.{praefix}_regler_i_anteil",
                f"input_number.{praefix}_klappe_soll",
                f"input_select.{praefix}_leistungsmodus",
                f"input_boolean.{praefix}_klappe_invertiert",
                f"input_number.{praefix}_aktor_y0",
                *self._parameter_ids, *zusatz,
            ) if eid
        }

    def tauschen(self, entitaeten: Entitaeten, zusatz: Iterable[str], zustaende: list[dict], jetzt: float) -> None:
        """Neue Zuweisung übernehmen. Läuft in der Warteschlange, also nie während eines Laufs."""
        if entitaeten.abgas != self.ent.abgas:
            self._weg_seit, self._weg_gemeldet = None, False
        if entitaeten.druck != self.ent.druck:
            self._druck_wert = self._druck_zeit = None
        if entitaeten.abgas != self.ent.abgas or entitaeten.t_wt != self.ent.t_wt:
            self.ableitung = Ableitung()
        self.ent = entitaeten
        self._relevant_setzen(zusatz)
        self.aufbauen(zustaende, jetzt)

    # ---- Spiegel füllen -----------------------------------------------

    def aufbauen(self, zustaende: list[dict], jetzt: float) -> None:
        """Ersetzt den Spiegel durch einen vollständigen Abruf. Löst keine Läufe aus."""
        alt = self._spiegel
        self._spiegel = {}
        for roh in zustaende:
            eid = roh.get("entity_id")
            if eid in self._relevant:
                self._uebernehmen(eid, roh, jetzt, alt.get(eid))
        for eid, feld in self._parameter_ids.items():
            if eid not in self._spiegel and eid not in self._parameter_gewarnt:
                self._parameter_gewarnt[eid] = "fehlt"
                log.warning("%s fehlt, %s bleibt %s", eid, feld, getattr(self.parameter, feld))

    def ereignis(self, daten: dict, jetzt: float) -> list[str]:
        """Verarbeitet state_changed und gibt die Auslöser zurück, wie die Trigger der Automation."""
        eid = daten.get("entity_id")
        if eid not in self._relevant:
            return []
        neu = daten.get("new_state")
        if neu is None:
            self._spiegel.pop(eid, None)
            return []
        alt = self._spiegel.get(eid)
        alter_wert = alt.zustand if alt else (daten.get("old_state") or {}).get("state")
        self._uebernehmen(eid, neu, jetzt, alt)
        wert = neu.get("state")

        if eid == self.ent.tuer:
            return [TUER]
        if eid == self.ent.wartung and wert != alter_wert:
            if wert == "on":
                return [WARTUNG_AN]
            if wert == "off":
                return [WARTUNG_AUS]
        if eid in self._bedienung_ids:
            return [BEDIENUNG]
        return []

    def _uebernehmen(self, eid: str, roh: dict, jetzt: float, alt: Optional[Eintrag]) -> None:
        wert = str(roh.get("state"))
        geaendert = _zeitstempel(roh.get("last_changed"))
        if geaendert is None:
            # Ohne Zeitstempel gilt der Empfang der letzten Wertänderung.
            geaendert = alt.geaendert if alt and alt.zustand == wert else jetzt
        gemeldet = (
            _zeitstempel(roh.get("last_reported"))
            or _zeitstempel(roh.get("last_updated"))
            or geaendert
        )
        self._spiegel[eid] = Eintrag(wert, roh.get("attributes") or {}, gemeldet, geaendert)

        if eid == self.ent.abgas:
            self._abgas_verfolgen(wert, geaendert)
            zahl = self.temperatur_c(eid)
            if zahl is not None:
                self.ableitung.abgas(gemeldet, zahl)
        elif eid == self.ent.druck:
            self._druck_verfolgen(wert, geaendert)
        if eid == self.ent.t_wt:
            zahl = als_zahl(wert)
            if zahl is not None:
                self.ableitung.twt(gemeldet, zahl)
        feld = self._parameter_ids.get(eid)
        if feld is not None:
            self._parameter_setzen(feld, eid, wert)

    def _abgas_verfolgen(self, wert: str, geaendert: float) -> None:
        if wert in _WEG_ZUSTAENDE:
            # Der Wechsel unavailable <-> unknown unterbricht die 60 s nicht.
            if self._weg_seit is None:
                self._weg_seit = geaendert
        else:
            self._weg_seit = None
            self._weg_gemeldet = False

    def _druck_verfolgen(self, wert: str, geaendert: float) -> None:
        # Ein Ausfall ist keine Druckänderung, sonst sähe das Alter frisch aus.
        zahl = als_zahl(wert)
        if zahl is not None and zahl != self._druck_wert:
            self._druck_wert = zahl
            self._druck_zeit = geaendert

    def _parameter_setzen(self, feld: str, eid: str, wert: str) -> None:
        zahl = als_zahl(wert)
        if zahl is None:
            if self._parameter_gewarnt.get(eid) != wert:
                self._parameter_gewarnt[eid] = wert
                log.warning(
                    "%s hat den ungültigen Wert %r, %s bleibt %s",
                    eid, wert, feld, getattr(self.parameter, feld),
                )
            return
        self._parameter_gewarnt.pop(eid, None)
        setattr(self.parameter, feld, zahl)

    # ---- Abfragen -----------------------------------------------------

    def pruefe_sensor_weg(self, jetzt: float) -> bool:
        """True genau einmal je Ausfall, sobald das Abgas 60 s durchgehend weg ist."""
        if self._weg_seit is None or self._weg_gemeldet:
            return False
        if jetzt - self._weg_seit < SENSOR_WEG_NACH_S:
            return False
        self._weg_gemeldet = True
        return True

    def _wert(self, eid: Optional[str]) -> Optional[str]:
        eintrag = self._spiegel.get(eid) if eid else None
        return eintrag.zustand if eintrag else None

    def _zahl(self, eid: Optional[str]) -> Optional[float]:
        return als_zahl(self._wert(eid))

    def eingaben(self, jetzt: float, ausloeser: str) -> Eingaben:
        abgas = self._spiegel.get(self.ent.abgas) if self.ent.abgas else None
        # Ganze Sekunden wie `| int` in der Automation.
        t_alter = float(int(max(0.0, jetzt - abgas.gemeldet))) if abgas else 0.0
        dp_alter = None if self._druck_zeit is None else max(0.0, jetzt - self._druck_zeit)
        return Eingaben(
            jetzt=jetzt,
            t=self.temperatur_c(self.ent.abgas),
            t_alter_s=t_alter,
            g=self._zahl(self.ent.gradient) or 0.0 if self.ent.gradient else self.ableitung.gradient(jetzt),
            tmax3=self._zahl(self.ent.t_max_3min) if self.ent.t_max_3min else self.ableitung.t_max_3min(jetzt),
            tuer_offen={"on": True, "off": False}.get(self._wert(self.ent.tuer)),
            twt_roh=self._zahl(self.ent.t_wt),
            twt_anstieg=(self._zahl(self.ent.t_wt_anstieg) or 0.0) if self.ent.t_wt_anstieg
            else self.ableitung.twt_anstieg(jetzt),
            puffer_unten=self._zahl(self.ent.puffer_unten) or 0.0,
            wartung=self._wert(self.ent.wartung) == "on",
            handbetrieb=self._wert(self.ent.handbetrieb) == "on",
            ausloeser=ausloeser,
            dp_alter_s=dp_alter,
            can_alter_s=self._zahl(self.ent.can_alter),
            # Nur ein klares „aus“ schaltet den Türkontakt ab.
            tuerkontakt_vorhanden=self._wert(self.ent.tuerkontakt_vorhanden) != "off",
        )

    def phase_fehlt(self) -> bool:
        """True, wenn es den Phasen-Helfer gar nicht gibt, etwa auf einer anderen Anlage."""
        return self._phase_id not in self._spiegel

    def eintrag(self, eid: Optional[str]) -> Optional[Eintrag]:
        return self._spiegel.get(eid) if eid else None

    def zahl(self, eid: Optional[str]) -> Optional[float]:
        return self._zahl(eid)

    def temperatur_c(self, eid: Optional[str]) -> Optional[float]:
        """Temperatur in °C, auch wenn der Sensor laut unit_of_measurement °F meldet."""
        eintrag = self.eintrag(eid)
        zahl = als_zahl(eintrag.zustand) if eintrag else None
        return None if zahl is None else temperatur_nach_c(zahl, eintrag.attribute.get("unit_of_measurement"))

    def stellung(self, eid: Optional[str]) -> Optional[float]:
        """Stellung in %. Cover und Licht melden sie im Attribut, nicht im Zustand."""
        eintrag = self.eintrag(eid)
        if eintrag is None or eintrag.zustand in _WEG_ZUSTAENDE:
            return None
        domain = eid.split(".", 1)[0]
        if domain == "cover":
            return als_zahl(eintrag.attribute.get("current_position"))
        if domain == "light":
            if eintrag.zustand == "off":
                return 0.0
            helligkeit = als_zahl(eintrag.attribute.get("brightness"))
            return None if helligkeit is None else float(round(helligkeit * 100 / 255))
        return als_zahl(eintrag.zustand)

    def klappe_ist(self, eid: Optional[str], profil: Anlagenprofil) -> Optional[float]:
        """Iststellung in %. Ein Sensor kann Volt oder mA melden, dann rechnet das Anlagenprofil um."""
        eintrag = self.eintrag(eid)
        if eintrag is None or eid.split(".", 1)[0] in ("cover", "light"):
            return self.stellung(eid)
        return rueckmeldung_prozent(self.stellung(eid), eintrag.attribute.get("unit_of_measurement"), profil)

    def wert(self, eid: Optional[str]) -> Optional[str]:
        return self._wert(eid)

    def startzustand_leer(self, jetzt: float) -> Zustand:
        return Zustand(phase=STANDBY, phase_seit=float(int(jetzt)))

    def startzustand(self, jetzt: float) -> Optional[Zustand]:
        """Zustand aus den Helfern, oder None, solange die Phase noch nicht lesbar ist."""
        phase = self._wert(self._phase_id)
        if phase not in PHASEN:
            return None
        eintrag = self._spiegel.get(self._phase_seit_id)
        phase_seit = als_zahl(eintrag.attribute.get("timestamp")) if eintrag else None
        if phase_seit is None:
            log.warning("%s ohne Attribut timestamp, Phase gilt ab jetzt", self._phase_seit_id)
            phase_seit = float(int(jetzt))

        def helfer(feld: str, vorgabe: float) -> float:
            eid = f"input_number.{self.praefix}_{feld}"
            zahl = self._zahl(eid)
            if zahl is None:
                log.warning("%s nicht lesbar, verwende %s", eid, vorgabe)
                return vorgabe
            return zahl

        leer = Zustand()
        return Zustand(
            phase=phase,
            phase_seit=phase_seit,
            t_maximum=helfer("t_maximum", leer.t_maximum),
            t_phasenstart=helfer("t_phasenstart", leer.t_phasenstart),
            i_anteil=helfer("regler_i_anteil", leer.i_anteil),
            klappe_soll=int(round(helfer("klappe_soll", leer.klappe_soll))),
        )

