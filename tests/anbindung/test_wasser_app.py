"""Die Wasserseite in der App: Status, Sensor, Push nur vom führenden Regelstand, Wärmemengenzähler."""
import pytest

from anbindung.ansicht import status
from kern.typen import NEU, PARITAET, TICK
from test_push import ablauf_mit, fabrik_mit, lauf
from testdaten import ENTITAETEN as E

ALLE_ARTEN = {"wasser_hinweis": ["notify.a"], "wasser_warnung": ["notify.a"], "wasser_alarm": ["notify.a"]}
T0 = 1_800_000_000.0


async def einrichten(app, ha, s, rollen=None, **profil):
    s.rollen_setzen({"abgas": E.abgas, "t_wt": "sensor.twt", "pumpe": "binary_sensor.pumpe", "vorlauf": "sensor.vl",
                     "puffer_unten": E.puffer_unten} | (rollen or {}), True)
    s.profil_setzen({"ofen": "wassergefuehrt", "puffer": True, "pumpe_extern": True} | profil)
    await app._wechseln()


def werte(ha, twt=60, vorlauf=70, pumpe="on", puffer=40):
    ha.setzen("sensor.twt", str(twt), {"unit_of_measurement": "°C"})
    ha.setzen("sensor.vl", str(vorlauf), {"unit_of_measurement": "°C"})
    ha.setzen("binary_sensor.pumpe", pumpe)
    ha.setzen(E.puffer_unten, str(puffer), {"unit_of_measurement": "°C"})


async def takt(app, jetzt):
    app.benachrichtiger._uhr = lambda: jetzt           # dieselbe Zeit für die Entprellung der Nachrichten
    await app._wasser_pruefen(jetzt)
    await app.benachrichtiger.abwarten()


def gesendet(ha, art=None):
    return [k["message"] for _, k in ha.gesendet()]


def test_luftgefuehrt_hat_keinen_wasserstatus_und_sendet_nichts():
    async def p(app, ha, s):
        await lauf(app)
        werte(ha, twt=99)
        await app._wasser_pruefen(T0)
        assert ha.geschrieben("sensor.kamin_app_wasser_status")[-1] == "aus"
        assert status(app)["wasser"] is None and ha.gesendet() == []
    ablauf_mit(p, fabrik_mit(), ALLE_ARTEN)


def test_alarm_geht_einmal_als_dringende_push_raus_und_der_sensor_zeigt_die_texte():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        await lauf(app)
        werte(ha, twt=90)
        await app._wechseln()
        await takt(app, T0)
        assert ha.gesendet() == [] and ha.geschrieben("sensor.kamin_app_wasser_status")[-1] == "ok"      # entprellt
        await takt(app, T0 + 30)
        await takt(app, T0 + 60)
        sendungen = ha.gesendet()
        assert len(sendungen) == 1 and "heißer als" in sendungen[0][1]["message"]
        assert sendungen[0][1]["data"] == {"push": {"interruption-level": "time-sensitive"}}
        assert ha.geschrieben("sensor.kamin_app_wasser_status")[-1] == "alarm"
        texte = [k for m, p_, k in ha.rest_anfragen if m == "POST" and p_.endswith("states/sensor.kamin_app_wasser_status")][-1]
        assert texte["attributes"]["texte"] and "Wärmetauscher" in texte["attributes"]["texte"][0]
        assert status(app)["wasser"]["stufe"] == "alarm"
    ablauf_mit(p, fabrik_mit(), ALLE_ARTEN, wasser=True)


def test_alarm_erinnert_nach_fuenfzehn_minuten_und_endet_mit_der_entwarnung():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        await lauf(app)
        werte(ha, twt=90)
        await app._wechseln()
        for t in (0, 30, 60):
            await takt(app, T0 + t)
        assert len(ha.gesendet()) == 1
        await takt(app, T0 + 600)
        assert len(ha.gesendet()) == 1
        await takt(app, T0 + 60 + 900)
        assert len(ha.gesendet()) == 2
        werte(ha, twt=70)
        await app._wechseln()
        await takt(app, T0 + 2000)
        assert ha.geschrieben("sensor.kamin_app_wasser_status")[-1] == "ok"
        werte(ha, twt=90)
        await app._wechseln()
        for t in (2100, 2130, 2160):
            await takt(app, T0 + t)
        assert len(ha.gesendet()) == 3          # ein neuer Alarm meldet sich sofort wieder
    ablauf_mit(p, fabrik_mit(), ALLE_ARTEN, wasser=True)


@pytest.mark.parametrize("zustand, art, text", [
    ({"vorlauf": 92}, "wasser_warnung", "hoch"),
    ({"puffer": 70}, "wasser_hinweis", "nicht nachlegen"),
    ({"twt": 95}, "wasser_alarm", "heißer"),
])
def test_stufe_bestimmt_die_art_der_nachricht(zustand, art, text):
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        await lauf(app)
        werte(ha, **zustand)
        await app._wechseln()
        for t in (0, 30, 60):
            await takt(app, T0 + t)
        assert len(ha.gesendet()) == 1 and text in ha.gesendet()[0][1]["message"]
    nur = {art: ["notify.a"]}
    ablauf_mit(p, fabrik_mit(), nur, wasser=True)


def test_art_ohne_zugeordnetes_geraet_sendet_nichts_der_sensor_zeigt_es_trotzdem():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        await lauf(app)
        werte(ha, twt=95)
        await app._wechseln()
        for t in (0, 30, 60):
            await takt(app, T0 + t)
        assert ha.gesendet() == [] and ha.geschrieben("sensor.kamin_app_wasser_status")[-1] == "alarm"
    ablauf_mit(p, fabrik_mit(), {"wasser_hinweis": ["notify.a"]}, wasser=True)


def test_ausloesung_nur_vom_fuehrenden_regelstand():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        werte(ha, twt=95)
        await app._wechseln()
        app.letzte.clear()                     # der führende Regelstand hat noch nichts geliefert
        for t in (0, 30, 60):
            await takt(app, T0 + t)
        assert ha.gesendet() == []
        await lauf(app, (NEU,))                # im Lauschen führt paritaet, neu zählt nicht
        await takt(app, T0 + 90)
        assert ha.gesendet() == []
        await lauf(app, (PARITAET,))
        await takt(app, T0 + 120)
        assert len(ha.gesendet()) == 1
    ablauf_mit(p, fabrik_mit(), ALLE_ARTEN, wasser=True)


def test_phase_des_fuehrenden_regelstands_steuert_die_pumpenregel():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        await lauf(app)                         # Phase aus dem Stand der Automation: Abbrand
        werte(ha, twt=75, pumpe="off")
        await app._wechseln()
        for t in (0, 60, 120, 150):
            await takt(app, T0 + t)
        assert "die Pumpe steht" in ha.gesendet()[0][1]["message"]
    ablauf_mit(p, fabrik_mit(), ALLE_ARTEN, wasser=True)


def test_eigene_grenzen_der_wasserseite_gelten():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        s.parameter_setzen("wasser_vorlauf_warnung", 80)
        await lauf(app)
        werte(ha, vorlauf=82)
        await app._wechseln()
        for t in (0, 30, 60):
            await takt(app, T0 + t)
        assert status(app)["wasser"]["stufe"] == "warnung"
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_waermeleistung_und_energie_des_tages_werden_umgerechnet_angezeigt():
    async def p(app, ha, s):
        await einrichten(app, ha, s, {"waermeleistung": "sensor.wl", "waermezaehler": "sensor.wz"})
        ha.setzen("sensor.wl", "4500", {"unit_of_measurement": "W"})
        ha.setzen("sensor.wz", "1000000", {"unit_of_measurement": "Wh"})
        await app._wechseln()
        app._uhr = lambda: T0
        await app._ausfuehren("tick", (PARITAET,))
        await app._ausfuehren(TICK, (PARITAET,))
        ha.setzen("sensor.wz", "1002.5", {"unit_of_measurement": "kWh"})
        await app._wechseln()
        app._uhr = lambda: T0 + 30
        await app._ausfuehren(TICK, (PARITAET,))
        w = status(app)["wasser"]
        assert w["leistung_kw"] == pytest.approx(4.5) and w["energie_heute_kwh"] == pytest.approx(2.5)
        assert w["leistung_problem"] is None and w["energie_problem"] is None
        ha.setzen("sensor.wl", "4.5", {"unit_of_measurement": "kWh"})          # falsche Einheit
        await app._wechseln()
        w = status(app)["wasser"]
        assert w["leistung_kw"] is None and "kWh" in w["leistung_problem"]
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_leistung_geht_nicht_in_die_buchfuehrung_ein():
    async def p(app, ha, s):
        await einrichten(app, ha, s, {"waermeleistung": "sensor.wl"})
        ha.setzen("sensor.wl", "9000", {"unit_of_measurement": "W"})
        await app._wechseln()
        for t in (0, 30):
            app._uhr = lambda t=t: T0 + t
            await app._ausfuehren(TICK, (PARITAET,))
        zeile = next(iter(app.buchfuehrung.tage.values()))
        assert zeile["waerme_zaehler_kwh"] is None and zeile["verbrauch_kwh"] is None
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_wasserseite_schaltet_nichts():
    async def p(app, ha, s):
        await einrichten(app, ha, s)
        await lauf(app)
        werte(ha, twt=99, pumpe="off")
        await app._wechseln()
        for t in (0, 60, 130, 200):
            await takt(app, T0 + t)
        assert ha.schaltbefehle() == []
    ablauf_mit(p, fabrik_mit(), ALLE_ARTEN, wasser=True)
