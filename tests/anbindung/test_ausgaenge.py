"""Ausgänge im Betrieb: Dienst je Domain, Senden bei Änderung, Abweichung, Aktionen, Schaltfehler."""
import asyncio

import pytest

from anbindung.ausgaenge import (Ausgaenge, begrenzen, klappen_befehl, zugewiesene_ausgaenge)
from kern.typen import AKTION_SNIFFER_RESTART, AKTION_WARTUNG_AUS

ROLLEN = {"ausgang_klappe": "cover.k", "ausgang_heartbeat": "number.hb", "ausgang_neustart": "button.n",
          "wartung": "input_boolean.w"}


class Stand:
    def __init__(self, rollen=None, ist=None, invertiert=False, y0=0.0, fehler=None):
        self.rollen = dict(ROLLEN if rollen is None else rollen)
        self.ist = ist
        self.kennlinie = (invertiert, y0)
        self.fehler = fehler
        self.aufrufe = []
        self.stoerungen = []
        self.jetzt = 1000.0
        self.a = Ausgaenge(self.schalten, lambda: self.rollen, lambda eid: self.ist, lambda: self.kennlinie,
                           lambda eid: self.attribute, self.stoerungen.append, lambda: self.jetzt, sicher_pause_s=0)
        self.attribute = {}

    async def schalten(self, domain, dienst, eid, daten):
        self.aufrufe.append((f"{domain}.{dienst}", eid, daten))
        if self.fehler:
            raise self.fehler


def lauf(koroutine):
    return asyncio.run(koroutine)


@pytest.mark.parametrize("eid, soll, erwartet", [
    ("cover.k", 37, ("cover", "set_cover_position", {"position": 37}, 37)),
    ("number.k", 0, ("number", "set_value", {"value": 0}, 0)),
    ("input_number.k", 100, ("input_number", "set_value", {"value": 100}, 100)),
    ("light.k", 50, ("light", "turn_on", {"brightness_pct": 50, "transition": 0}, 50)),
    ("light.k", 100, ("light", "turn_on", {"brightness_pct": 100, "transition": 0}, 100)),
    ("light.k", 0, ("light", "turn_off", None, 0)),     # 0 ist beim Licht sicher „zu“
])
def test_dienst_je_domain(eid, soll, erwartet):
    assert klappen_befehl(eid, soll) == erwartet


def test_domain_ohne_stellmoeglichkeit():
    assert klappen_befehl("switch.k", 50) is None


@pytest.mark.parametrize("soll, y0, helligkeit", [(100, 2, 100), (50, 2, 60), (1, 2, 21), (10, 2, 28)])
def test_startspannung_wie_automation(soll, y0, helligkeit):
    assert klappen_befehl("light.k", soll, False, y0)[2]["brightness_pct"] == helligkeit


def test_startspannung_null_bleibt_aus():
    assert klappen_befehl("light.k", 0, False, 2)[1] == "turn_off"


def test_invertiert_100_prozent_heisst_ausgabe_null():
    assert klappen_befehl("cover.k", 100, True)[2] == {"position": 0}
    assert klappen_befehl("light.k", 100, True)[1] == "turn_off"
    assert klappen_befehl("light.k", 0, True, 0)[2]["brightness_pct"] == 100


@pytest.mark.parametrize("roh, wert", [(-5, 0), (140, 100), (55.4, 55), ("x", 100), (None, 100), (float("nan"), 100)])
def test_unbrauchbares_soll_oeffnet(roh, wert):
    assert begrenzen(roh) == wert


def test_senden_bei_aenderung_und_spaetestens_alle_5_min():
    async def ablauf():
        s = Stand()
        assert await s.a.klappe(40, 1000)
        assert await s.a.klappe(40, 1299)
        assert len(s.aufrufe) == 1
        await s.a.klappe(41, 1299)
        assert len(s.aufrufe) == 2
        await s.a.klappe(41, 1599)
        assert len(s.aufrufe) == 3
        assert s.a.gesendet == 41
    lauf(ablauf())


def test_fehlgeschlagener_befehl_wird_wiederholt_und_bricht_nichts_ab():
    async def ablauf():
        s = Stand(fehler=OSError("weg"))
        assert not await s.a.klappe(40, 1000)
        assert not await s.a.klappe(40, 1001)
        assert len(s.aufrufe) == 2 and s.a.gesendet is None
        s.fehler = None
        assert await s.a.klappe(40, 1002)
        assert s.a.gesendet == 40
    lauf(ablauf())


def test_mehrere_fehler_in_folge_sind_eine_stoerung():
    async def ablauf():
        s = Stand(fehler=OSError("weg"))
        for t in range(2):
            await s.a.klappe(40, 1000 + t)
        assert s.stoerungen == []
        for t in range(2, 5):
            await s.a.klappe(40, 1000 + t)
        assert len(s.stoerungen) == 1 and s.a.stoerung and "keine Befehle" in s.a.stoerung
        s.fehler = None
        await s.a.klappe(40, 1010)
        assert s.a.stoerung is None
    lauf(ablauf())


def test_abweichung_ueber_10_prozent_laenger_als_2_min():
    async def ablauf():
        s = Stand(rollen={**ROLLEN, "klappe_ist": "sensor.ist"}, ist=20)
        await s.a.klappe(40, 1000)
        await s.a.klappe(40, 1120)
        assert s.stoerungen == []
        await s.a.klappe(40, 1121)
        assert len(s.stoerungen) == 1 and "folgt nicht" in s.stoerungen[0]
        await s.a.klappe(40, 1200)
        assert len(s.stoerungen) == 1
        s.ist = 35
        await s.a.klappe(40, 1210)
        assert s.a.stoerung is None
    lauf(ablauf())


def test_abweichung_innerhalb_10_prozent_und_ohne_rueckmeldung_still():
    async def ablauf():
        s = Stand(rollen={**ROLLEN, "klappe_ist": "sensor.ist"}, ist=30)
        await s.a.klappe(40, 1000)
        await s.a.klappe(40, 1500)
        s.ist = None
        await s.a.klappe(40, 2000)
        assert s.stoerungen == []
    lauf(ablauf())


def test_rueckmeldung_des_ausgangs_selbst_vergleicht_die_ausgabe():
    async def ablauf():
        s = Stand(rollen={**ROLLEN, "klappe_ist": "cover.k"}, ist=70, invertiert=True)
        await s.a.klappe(30, 1000)
        await s.a.klappe(30, 1200)
        assert s.stoerungen == []
    lauf(ablauf())


def test_heartbeat_wechselt_immer_und_bleibt_im_bereich():
    async def ablauf():
        s = Stand()
        s.attribute = {"min": 5, "max": 8}
        for t in range(9):
            await s.a.heartbeat(1000 + t)
        werte = [a[2]["value"] for a in s.aufrufe]
        assert all(a[0] == "number.set_value" and a[1] == "number.hb" for a in s.aufrufe)
        assert all(5 <= w <= 8 for w in werte) and all(x != y for x, y in zip(werte, werte[1:]))
    lauf(ablauf())


@pytest.mark.parametrize("aktion, rollen, erwartet", [
    (AKTION_WARTUNG_AUS, {"wartung": "input_boolean.w"}, ("input_boolean.turn_off", "input_boolean.w", None)),
    (AKTION_WARTUNG_AUS, {"wartung": "switch.w"}, ("switch.turn_off", "switch.w", None)),
    (AKTION_SNIFFER_RESTART, {"ausgang_neustart": "button.n"}, ("button.press", "button.n", None)),
    (AKTION_SNIFFER_RESTART, {"ausgang_neustart": "script.n"}, ("script.turn_on", "script.n", None)),
])
def test_aktion_geht_an_ihre_rolle(aktion, rollen, erwartet):
    s = Stand(rollen=rollen)
    assert lauf(s.a.aktion(aktion)) and s.aufrufe == [erwartet]


@pytest.mark.parametrize("aktion", [AKTION_WARTUNG_AUS, AKTION_SNIFFER_RESTART])
def test_fehlende_rolle_wird_geloggt_und_nichts_geschaltet(aktion, caplog):
    s = Stand(rollen={})
    assert not lauf(s.a.aktion(aktion)) and s.aufrufe == []
    assert any("nicht ausgeführt" in r.getMessage() for r in caplog.records)


def test_ohne_klappenrolle_und_watchdogrolle_wird_nichts_geschaltet():
    async def ablauf():
        s = Stand(rollen={})
        assert not await s.a.klappe(40, 1000) and not await s.a.heartbeat(1000) and not await s.a.sicher_oeffnen()
        assert s.aufrufe == []
    lauf(ablauf())


def test_sicher_oeffnen_sendet_auch_ohne_aenderung_und_versucht_es_mehrfach():
    async def ablauf():
        s = Stand()
        await s.a.klappe(100, 1000)
        s.fehler = OSError("weg")
        assert not await s.a.sicher_oeffnen()
        assert len(s.aufrufe) == 4
        s.fehler = None
        assert await s.a.sicher_oeffnen()
        assert s.aufrufe[-1] == ("cover.set_cover_position", "cover.k", {"position": 100})
    lauf(ablauf())


def test_zugewiesene_ausgaenge_ohne_reine_eingaenge():
    rollen = {**ROLLEN, "pumpe": "switch.pumpe", "klappe_ist": "cover.k", "abgas": "sensor.a"}
    assert zugewiesene_ausgaenge(rollen) == {"cover.k", "number.hb", "button.n", "input_boolean.w"}
    # Wer dieselbe Entität als Pumpe und Wartung einträgt, bekommt sie nicht schaltbar.
    gleich = {"pumpe": "switch.x", "wartung": "switch.x", "ausgang_klappe": "cover.k"}
    assert zugewiesene_ausgaenge(gleich) == {"cover.k"}


def test_stoerung_quittieren_setzt_zurueck_und_meldet_erneut():
    async def ablauf():
        s = Stand(fehler=OSError("weg"))
        for t in range(3):
            await s.a.klappe(40, 1000 + t)
        assert s.a.stoerung
        assert "keine Befehle" in s.a.stoerung_quittieren()
        assert s.a.stoerung is None
        for t in range(3):
            await s.a.klappe(40, 1010 + t)
        assert len(s.stoerungen) == 2
    lauf(ablauf())
