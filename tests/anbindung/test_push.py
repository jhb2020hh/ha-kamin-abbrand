"""Auslöser der Push-Nachrichten in der App und die Reinigungserinnerung."""
import asyncio
import tempfile
from functools import partial
from pathlib import Path

from anbindung.ansicht import NACHLEGEN_TEXT
from anbindung.eingaenge import Eingaenge
from anbindung.einstellungen import Speicher
from anbindung.ha_client import HaClient
from anbindung.main import App
from anbindung.schatten import Verlauf
from fake_ha import TOKEN, FakeHa, warte_auf
from kern.phasen import TITEL, TITEL_ANHEIZFEHLER, TITEL_STOERUNG
from kern.typen import NEU, PARITAET, Ergebnis, Meldung
from test_app import helfer_setzen
from testdaten import ENTITAETEN as E

H = 3600.0


class Skript:
    """Steuerung, die je Regelstand dieselbe Meldung und denselben Übergang wiederholt."""
    def __init__(self, zustand, meldungen, uebergang):
        self.zustand, self.meldungen, self.uebergang = zustand, meldungen, uebergang

    def schritt(self, eingaben):
        return Ergebnis(zustand=self.zustand, klappe=50, meldungen=list(self.meldungen), uebergang=self.uebergang)


def fabrik_mit(meldungen=(), uebergang=None, nur=(PARITAET, NEU)):
    def bauen(parameter, zustand, regelstand, takt_s):
        if regelstand in nur:
            return Skript(zustand, meldungen, uebergang)
        return Skript(zustand, [], None)
    return bauen


def ablauf_mit(pruefung, fabrik, zuordnung, puffer="40", wasser=False, betrieb_h=0.0, **ha_einstellung):
    async def ablauf(tmp):
        async with FakeHa() as ha:
            helfer_setzen(ha)
            ha.setzen(E.puffer_unten, puffer, {"unit_of_measurement": "°C"})
            s = Speicher(tmp / "e.json")
            s.rollen_setzen({"abgas": E.abgas, "puffer_unten": E.puffer_unten}, True)
            s.benachrichtigungen_setzen(zuordnung)
            if wasser:
                s.profil_setzen({"ofen": "wassergefuehrt"})
            s.daten["betrieb_s"] = betrieb_h * H
            app = App(partial(HaClient, TOKEN, ws_url=ha.ws_url, rest_url=ha.rest_url, backoff_start_s=0.01),
                      Eingaenge(s.entitaeten(), "kamin_abbrand"), Verlauf(tmp), steuerung_fabrik=fabrik,
                      speicher=s, takt_paritaet_s=3600.0, takt_neu_s=3600.0, wache_intervall_s=0.01)
            stopp = asyncio.Event()
            aufgabe = asyncio.create_task(app.laufen(stopp))
            try:
                await warte_auf(lambda: app.client.verbunden and PARITAET in app.steuerungen)
                await pruefung(app, ha, s)
            finally:
                stopp.set()
                await aufgabe

    with tempfile.TemporaryDirectory() as ordner:
        asyncio.run(ablauf(Path(ordner)))


async def lauf(app, regelstaende=(PARITAET,)):
    await app._ausfuehren("test", regelstaende)
    await app.benachrichtiger.abwarten()


def test_fuehrender_regelstand_ist_vorerst_paritaet():
    async def p(app, ha, s):
        assert app.fuehrender_regelstand == PARITAET
    ablauf_mit(p, fabrik_mit(), {})


def test_kern_stoerungen_gehen_nur_vom_fuehrenden_regelstand_raus():
    async def p(app, ha, s):
        await lauf(app, (NEU,))
        assert ha.gesendet() == []
        await lauf(app, (PARITAET,))
        sendungen = ha.gesendet()
        assert [d for d, _ in sendungen] == ["notify.a"] * 2
        assert {k["title"] for _, k in sendungen} == {TITEL_STOERUNG, TITEL_ANHEIZFEHLER}
        assert all(k["data"] == {"push": {"interruption-level": "time-sensitive"}} for _, k in sendungen)
    meldungen = [Meldung(TITEL_STOERUNG, "Kein Messwert"), Meldung(TITEL_ANHEIZFEHLER, "Feuer prüfen")]
    ablauf_mit(p, fabrik_mit(meldungen), {"stoerung": ["notify.a"]})


def test_normalbetrieb_meldungen_des_kerns_sind_keine_stoerung():
    async def p(app, ha, s):
        await lauf(app)
        assert ha.gesendet() == []
    meldungen = [Meldung(TITEL, "🪵 Glutphase: jetzt nachlegen"), Meldung(TITEL, "LUC-Messwerte wieder da"),
                 Meldung(TITEL, "Wartungsmodus nach Timeout automatisch beendet.")]
    ablauf_mit(p, fabrik_mit(meldungen), {"stoerung": ["notify.a"]})


def test_gleiche_stoerung_im_naechsten_lauf_wird_entprellt():
    async def p(app, ha, s):
        await lauf(app)
        await lauf(app)
        assert len(ha.gesendet()) == 1
    ablauf_mit(p, fabrik_mit([Meldung(TITEL_STOERUNG, "Kein Messwert")]), {"stoerung": ["notify.a"]})


def test_nachlegen_beim_eintritt_in_die_glut():
    async def p(app, ha, s):
        await lauf(app)
        assert ha.gesendet() == [("notify.a", {"title": "🔥 Kamin", "message": NACHLEGEN_TEXT})]
    ablauf_mit(p, fabrik_mit(uebergang=("Ausbrand", "Glut")), {"nachlegen": ["notify.a"]})


def test_nachlegen_nicht_bei_vollem_puffer_und_nicht_bei_anderen_uebergaengen():
    async def voll(app, ha, s):
        await lauf(app)
        assert ha.gesendet() == []
    ablauf_mit(voll, fabrik_mit(uebergang=("Ausbrand", "Glut")), {"nachlegen": ["notify.a"]}, puffer="75")

    async def anders(app, ha, s):
        await lauf(app)
        assert ha.gesendet() == []
    ablauf_mit(anders, fabrik_mit(uebergang=("Glut", "Abkühlen")), {"nachlegen": ["notify.a"]})


def test_nachlegen_vom_neuen_regelstand_wird_nicht_gesendet():
    async def p(app, ha, s):
        await lauf(app, (NEU,))
        assert ha.gesendet() == []
    ablauf_mit(p, fabrik_mit(uebergang=("Ausbrand", "Glut"), nur=(NEU,)), {"nachlegen": ["notify.a"]})


def test_ohne_zuordnung_wird_nichts_gesendet():
    async def p(app, ha, s):
        await lauf(app)
        assert ha.gesendet() == []
    ablauf_mit(p, fabrik_mit([Meldung(TITEL_STOERUNG, "x")], ("Ausbrand", "Glut")), {})


def test_sendefehler_bricht_den_lauf_nicht_ab():
    async def p(app, ha, s):
        ha.rest_status = 500
        await lauf(app)
        assert PARITAET in app.letzte and not app._fehler
        assert len(ha.gesendet()) == 1      # der Versuch fand statt
    ablauf_mit(p, fabrik_mit([Meldung(TITEL_STOERUNG, "x")]), {"stoerung": ["notify.a"]})


# ---- Reinigung ------------------------------------------------------------

def test_reinigung_wird_beim_faelligwerden_gemeldet_und_dann_alle_24_h():
    async def p(app, ha, s):
        zeit = [app._uhr()]
        app._uhr = lambda: zeit[0]
        app.benachrichtiger._uhr = app._uhr
        await lauf(app)
        assert [d for d, _ in ha.gesendet()] == ["notify.a"]
        assert "100 Brennstunden" in ha.gesendet()[0][1]["message"]
        assert s.wartungsstand.erinnert == zeit[0]
        zeit[0] += 23 * H
        await lauf(app)
        assert len(ha.gesendet()) == 1
        zeit[0] += 1 * H
        await lauf(app)
        assert len(ha.gesendet()) == 2
        await app.reinigung_bestaetigen()
        zeit[0] += 48 * H
        await lauf(app)
        assert len(ha.gesendet()) == 2
        assert s.wartungsstand.letzte_reinigung is not None
    ablauf_mit(p, fabrik_mit(), {"reinigung": ["notify.a"]}, wasser=True, betrieb_h=100.0)


def test_reinigung_erinnerung_ueberlebt_neustart():
    async def p(app, ha, s):
        s.daten["wartung"]["basis_s"] = -100 * H       # 100 Brennstunden seit der Basis
        await lauf(app)
        assert len(ha.gesendet()) == 1
        neu = Speicher(s.pfad)
        assert neu.wartungsstand.erinnert == s.wartungsstand.erinnert is not None
    ablauf_mit(p, fabrik_mit(), {"reinigung": ["notify.a"]}, wasser=True)


def test_reinigung_nicht_bei_luftgefuehrt_und_nicht_bei_intervall_null():
    async def luft(app, ha, s):
        s.daten["wartung"]["basis_s"] = -500 * H
        await lauf(app)
        assert ha.gesendet() == []
        assert ha.geschrieben("sensor.kamin_app_wt_reinigung")[-1] == "aus"
    ablauf_mit(luft, fabrik_mit(), {"reinigung": ["notify.a"]})

    async def null(app, ha, s):
        s.daten["wartung"]["basis_s"] = -500 * H
        s.parameter_setzen("wt_reinigung_h", 0)
        await lauf(app)
        assert ha.gesendet() == []
        assert ha.geschrieben("sensor.kamin_app_wt_reinigung")[-1] == "aus"
    ablauf_mit(null, fabrik_mit(), {"reinigung": ["notify.a"]}, wasser=True)


def test_sensor_reinigung_ok_und_faellig_mit_attributen():
    async def p(app, ha, s):
        s.daten["wartung"]["basis_s"] = -30 * H
        await lauf(app)
        assert ha.geschrieben("sensor.kamin_app_wt_reinigung")[-1] == "ok"
        s.daten["wartung"]["basis_s"] = -100 * H
        await lauf(app)
        assert ha.geschrieben("sensor.kamin_app_wt_reinigung")[-1] == "faellig"
        attribute = [k["attributes"] for m, pf, k in ha.rest_anfragen if pf.endswith("wt_reinigung")][-1]
        assert attribute["reststunden"] == 0 and attribute["intervall_stunden"] == 100
        assert attribute["letzte_reinigung"] is None
        await app.reinigung_bestaetigen()
        assert ha.geschrieben("sensor.kamin_app_wt_reinigung")[-1] == "ok"
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_wechsel_auf_wassergefuehrt_beginnt_die_zaehlung_neu(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.daten["betrieb_s"] = 400 * H
    s.profil_setzen({"ofen": "wassergefuehrt"})
    assert s.wartungsstand.basis_s == 400 * H
    s.daten["betrieb_s"] = 450 * H
    s.profil_setzen({"ofen": "wassergefuehrt", "waehrung": "CHF"})      # kein Wechsel, kein Zurücksetzen
    assert s.wartungsstand.basis_s == 400 * H


def test_intervall_vorgabe_eigener_wert_und_grenzen(tmp_path):
    import pytest
    from anbindung.einstellungen import EinstellungsFehler
    s = Speicher(tmp_path / "e.json")
    assert s.reinigung_intervall_h == 100
    assert s.parameter_setzen("wt_reinigung_h", 250) == 250 and s.reinigung_intervall_h == 250
    assert Speicher(s.pfad).reinigung_intervall_h == 250
    assert s.parameter_setzen("wt_reinigung_h", None) is None and s.reinigung_intervall_h == 100
    assert s.parameter_setzen("wt_reinigung_h", 0) == 0 and s.reinigung_intervall_h == 0
    for falsch in (-1, "abc", 99999):
        with pytest.raises(EinstellungsFehler):
            s.parameter_setzen("wt_reinigung_h", falsch)
    assert "wt_reinigung_h" not in s.parameter_neu       # gehört nicht zu den Parametern des Kerns
