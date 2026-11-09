"""Was die App im Betrieb schaltet: Klappe, Watchdog und die Aktionen des Kerns.

Nur der Schaltweg des Clients führt nach außen, und der ist im Lauschen gesperrt.
Ein Schaltfehler bricht keinen Regellauf ab: Er wird gezählt, und der nächste Lauf versucht es erneut.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Mapping, Optional

from anbindung.eingaenge import als_zahl
from kern.typen import AKTION_SNIFFER_RESTART, AKTION_WARTUNG_AUS

log = logging.getLogger(__name__)

KLAPPE_WIEDERHOLEN_S = 300.0
ABWEICHUNG_PROZENT = 10.0
ABWEICHUNG_DAUER_S = 120.0
FEHLER_GRENZE = 3
SICHER_VERSUCHE = 3
SICHER_PAUSE_S = 1.0
HEARTBEAT_BEREICH = (0, 1000)   # Wenn die Entität keine Grenzen meldet
Y0_MAX_V = 9.5

KLAPPE, HEARTBEAT, NEUSTART, WARTUNG_ROLLE, KLAPPE_IST = (
    "ausgang_klappe", "ausgang_heartbeat", "ausgang_neustart", "wartung", "klappe_ist")
AUSGANG_ROLLEN = (KLAPPE, HEARTBEAT, NEUSTART, WARTUNG_ROLLE)

_AKTIONEN: dict[str, tuple[str, dict[str, str]]] = {
    AKTION_WARTUNG_AUS: (WARTUNG_ROLLE, {"input_boolean": "turn_off", "switch": "turn_off"}),
    AKTION_SNIFFER_RESTART: (NEUSTART, {"button": "press", "script": "turn_on"}),
}

Schalter = Callable[[str, str, str, Optional[dict]], Awaitable[None]]
Befehl = tuple[str, str, Optional[dict], int]


def zugewiesene_ausgaenge(rollen: Mapping[str, str]) -> set[str]:
    """Die Liste für den Client. Was eine reine Eingangsrolle belegt, etwa die Pumpe, ist nie schaltbar."""
    nur_lesen = {eid for name, eid in rollen.items() if name not in AUSGANG_ROLLEN and name != KLAPPE_IST}
    return {eid for name in AUSGANG_ROLLEN if (eid := rollen.get(name)) and eid not in nur_lesen}


def begrenzen(wert: object) -> int:
    """0 bis 100. Ein unbrauchbarer Wert wird zu 100: Im Zweifel bleibt die Klappe offen."""
    zahl = als_zahl(wert)
    return 100 if zahl is None else int(round(min(max(zahl, 0.0), 100.0)))


def ausgabe_von(soll: int, invertiert: bool) -> int:
    return 100 - soll if invertiert else soll


def klappen_befehl(eid: str, soll: object, invertiert: bool = False, y0_v: float = 0.0) -> Optional[Befehl]:
    """(Domain, Dienst, Daten, Ausgabewert) oder None für eine Domain, die nicht stellen kann.

    Antriebe mit 2-10-V-Eingang brauchen den Offset y0_v, sonst schließen sie weiter als gewollt.
    Ausgabe 0 geht beim Licht als turn_off raus, damit es sicher „zu“ ist.
    """
    domain = eid.split(".", 1)[0]
    ausgabe = ausgabe_von(begrenzen(soll), invertiert)
    if domain == "cover":
        return domain, "set_cover_position", {"position": ausgabe}, ausgabe
    if domain in ("number", "input_number"):
        return domain, "set_value", {"value": ausgabe}, ausgabe
    if domain != "light":
        return None
    if ausgabe <= 0:
        return domain, "turn_off", None, 0
    offset = min(max(y0_v, 0.0), Y0_MAX_V) * 10
    return domain, "turn_on", {"brightness_pct": max(1, round(offset + (100 - offset) * ausgabe / 100)), "transition": 0}, ausgabe


class Ausgaenge:
    def __init__(
        self,
        schalten: Schalter,
        rollen: Callable[[], Mapping[str, str]],
        stellung: Callable[[Optional[str]], Optional[float]],
        kennlinie: Callable[[], tuple[bool, float]],
        attribute: Callable[[str], Mapping],
        stoerung_melden: Callable[[str], None],
        uhr: Callable[[], float],
        *,
        wiederholen_s: float = KLAPPE_WIEDERHOLEN_S,
        abweichung_dauer_s: float = ABWEICHUNG_DAUER_S,
        fehler_grenze: int = FEHLER_GRENZE,
        sicher_pause_s: float = SICHER_PAUSE_S,
    ) -> None:
        self._schalten = schalten
        self._rollen = rollen
        self._stellung = stellung
        self._kennlinie = kennlinie
        self._attribute = attribute
        self._stoerung_melden = stoerung_melden
        self._uhr = uhr
        self._wiederholen_s = wiederholen_s
        self._abweichung_dauer_s = abweichung_dauer_s
        self._fehler_grenze = fehler_grenze
        self._sicher_pause_s = sicher_pause_s
        self.gesendet: Optional[int] = None
        self._ausgabe: Optional[int] = None
        self._zeit = 0.0
        self._fehlschlaege = 0
        self._zaehler: Optional[int] = None
        self._abweichung_seit: Optional[float] = None
        self.stoerung_schalten: Optional[str] = None
        self.stoerung_abweichung: Optional[str] = None
        self._ohne_rolle_geloggt: set[str] = set()

    @property
    def stoerung(self) -> Optional[str]:
        return self.stoerung_schalten or self.stoerung_abweichung

    def vergessen(self) -> None:
        """Nach einem Neustart von HA oder beim Einschalten: Der nächste Lauf sendet in jedem Fall."""
        self.gesendet = self._ausgabe = None
        self._zeit = 0.0
        self._abweichung_seit = None
        self._zaehler = None

    def zuruecksetzen(self) -> None:
        self.vergessen()
        self._fehlschlaege = 0
        self.stoerung_schalten = self.stoerung_abweichung = None

    def _rolle(self, name: str) -> Optional[str]:
        eid = self._rollen().get(name)
        if not eid and name not in self._ohne_rolle_geloggt:
            self._ohne_rolle_geloggt.add(name)
            log.warning("Rolle %s ist nicht zugewiesen, es wird nichts geschaltet", name)
        return eid

    async def _rufen(self, domain: str, dienst: str, eid: str, daten: Optional[dict]) -> bool:
        try:
            await self._schalten(domain, dienst, eid, daten)
        except Exception as fehler:     # Auch ein Fehler hier darf keinen Regellauf berühren
            log.warning("%s.%s auf %s fehlgeschlagen: %r", domain, dienst, eid, fehler)
            return False
        return True

    async def klappe(self, soll: object, jetzt: float, zwingen: bool = False) -> bool:
        """Sendet bei Änderung und spätestens alle 5 min. False, wenn der Befehl nicht ankam."""
        eid = self._rolle(KLAPPE)
        if not eid:
            return False
        wert = begrenzen(soll)
        if not zwingen and wert == self.gesendet and jetzt - self._zeit < self._wiederholen_s:
            self._rueckmeldung_pruefen(jetzt)
            return True
        invertiert, y0_v = self._kennlinie()
        befehl = klappen_befehl(eid, wert, invertiert, y0_v)
        if befehl is None:
            log.error("%s kann nicht als Klappe gestellt werden", eid)
            return False
        domain, dienst, daten, ausgabe = befehl
        if not await self._rufen(domain, dienst, eid, daten):
            self._fehlschlaege += 1
            if self._fehlschlaege >= self._fehler_grenze and self.stoerung_schalten is None:
                self.stoerung_schalten = f"Die Klappe nimmt keine Befehle an ({self._fehlschlaege} Versuche ohne Erfolg)."
                self._stoerung_melden(self.stoerung_schalten)
            return False
        self._fehlschlaege = 0
        self.stoerung_schalten = None
        self.gesendet, self._ausgabe, self._zeit = wert, ausgabe, jetzt
        self._rueckmeldung_pruefen(jetzt)
        return True

    def _rueckmeldung_pruefen(self, jetzt: float) -> None:
        ist_eid = self._rollen().get(KLAPPE_IST)
        ist = self._stellung(ist_eid) if ist_eid else None
        if ist is None or self.gesendet is None:
            self._abweichung_seit = None
            return
        # Meldet der Ausgang sich selbst zurück, sieht man dort die Ausgabe, nicht die Stellung.
        erwartet = self._ausgabe if ist_eid == self._rollen().get(KLAPPE) else self.gesendet
        if abs(ist - erwartet) <= ABWEICHUNG_PROZENT:
            self._abweichung_seit = None
            self.stoerung_abweichung = None
            return
        if self._abweichung_seit is None:
            self._abweichung_seit = jetzt
        if jetzt - self._abweichung_seit > self._abweichung_dauer_s and self.stoerung_abweichung is None:
            self.stoerung_abweichung = f"Die Klappe folgt nicht: Soll {erwartet:.0f} %, Ist {ist:.0f} %."
            self._stoerung_melden(self.stoerung_abweichung)

    async def heartbeat(self, jetzt: float) -> bool:
        """Immer ein neuer Wert: Der Watchdog erkennt das Lebenszeichen an der Änderung."""
        eid = self._rolle(HEARTBEAT)
        if not eid:
            return False
        attribute = self._attribute(eid)
        minimum = als_zahl(attribute.get("min"))
        maximum = als_zahl(attribute.get("max"))
        unten, oben = (minimum, maximum) if minimum is not None and maximum is not None and maximum > minimum else HEARTBEAT_BEREICH
        spanne = max(int(oben - unten), 1)
        self._zaehler = int(jetzt) if self._zaehler is None else self._zaehler + 1
        return await self._rufen("number", "set_value", eid, {"value": int(unten) + self._zaehler % (spanne + 1)})

    async def aktion(self, name: str) -> bool:
        """Eine Aktion des Kerns an ihre Rolle. Fehlt die Rolle, wird das geloggt und nichts geschaltet."""
        ziel = _AKTIONEN.get(name)
        if ziel is None:
            log.error("Unbekannte Aktion %r", name)
            return False
        rolle, dienste = ziel
        eid = self._rolle(rolle)
        if not eid:
            log.warning("Aktion %s nicht ausgeführt: Rolle %s fehlt", name, rolle)
            return False
        domain = eid.split(".", 1)[0]
        dienst = dienste.get(domain)
        if dienst is None:
            log.error("Aktion %s: %s lässt sich nicht schalten", name, eid)
            return False
        return await self._rufen(domain, dienst, eid, None)

    async def fuehler_neustart(self) -> bool:
        return await self.aktion(AKTION_SNIFFER_RESTART)

    async def sicher_oeffnen(self) -> bool:
        """Klappe auf 100 %, auch ohne Änderung, mit ein paar Versuchen."""
        if not self._rollen().get(KLAPPE):
            return False
        for versuch in range(SICHER_VERSUCHE):
            if await self.klappe(100, self._uhr(), zwingen=True):
                return True
            if versuch + 1 < SICHER_VERSUCHE:
                await asyncio.sleep(self._sicher_pause_s)
        return False
