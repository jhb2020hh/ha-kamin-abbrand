"""Die Buchführung in der App: Auflagen vom führenden Regelstand, Zähler im Takt, Sensor für die Amortisation."""
import pytest

from anbindung.main import App
from kern.typen import NEU, PARITAET, TICK
from test_push import ablauf_mit, fabrik_mit, lauf

ANHEIZEN_ABBRAND = ("Anheizen", "Abbrand")
HOLZ = {"preis": 0.2, "preis_einheit": "kg", "heizwert": 4.0, "nutzungsgrad_ofen": 0.8, "kg_je_auflage": 5}
ERZEUGER = {"art": "erdgas", "anteil": 100, "preis": 0.10, "preis_einheit": "kWh", "nutzungsgrad": 0.9}


def einrichten(s):
    s.profil_setzen({"ofen": "luftgefuehrt", "holz": HOLZ, "erzeuger": [ERZEUGER]})


def auflagen(app):
    return sum(z["auflagen"] for z in app.buchfuehrung.tage.values())


def test_auflage_zaehlt_den_uebergang_anheizen_abbrand_im_lauschen_von_paritaet():
    async def p(app, ha, s):
        einrichten(s)
        await lauf(app, (NEU,))
        assert auflagen(app) == 0                        # im Lauschen führt paritaet
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1
        z = next(iter(app.buchfuehrung.tage.values()))
        assert z["holz_kg"] == 5 and z["holz_eur"] == 1.0
    ablauf_mit(p, fabrik_mit(uebergang=ANHEIZEN_ABBRAND), {})


def test_auflage_zaehlt_im_betrieb_vom_neuen_regelstand():
    async def p(app, ha, s):
        einrichten(s)
        original = App.fuehrender_regelstand
        App.fuehrender_regelstand = property(lambda self: NEU)      # Betrieb ohne die ganze Prüfkette: es geht um den Zähler
        try:
            await lauf(app, (PARITAET,))
            assert auflagen(app) == 0
            await lauf(app, (NEU,))
            assert auflagen(app) == 1
        finally:
            App.fuehrender_regelstand = original
    ablauf_mit(p, fabrik_mit(uebergang=ANHEIZEN_ABBRAND), {})


def test_auflage_ohne_tueroeffnung_zaehlt_nicht_und_schueren_auch_nicht():
    async def p(app, ha, s):
        einrichten(s)
        s.rollen_setzen(s.rollen | {"tuer": "binary_sensor.tuer"}, True)
        zeit = [1_800_000_000.0]
        app._uhr = lambda: zeit[0]
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1                        # nach dem Start gilt die Tür als geöffnet
        zeit[0] += 1800
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1                        # Glut flammt auf, die Tür blieb zu: kein Holz
        app._tuer_merken(True, zeit[0] - 1500)
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1                        # Tür 5 min nach der Auflage: geschürt
        app._tuer_merken(True, zeit[0])
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 2
    ablauf_mit(p, fabrik_mit(uebergang=ANHEIZEN_ABBRAND), {})


def test_ohne_tuerkontakt_zaehlt_jeder_uebergang():
    async def p(app, ha, s):
        einrichten(s)
        s.rollen_setzen(s.rollen, False)
        await lauf(app, (PARITAET,))
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 2
    ablauf_mit(p, fabrik_mit(uebergang=ANHEIZEN_ABBRAND), {})


def test_andere_uebergaenge_sind_keine_auflage():
    async def p(app, ha, s):
        einrichten(s)
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 0
    ablauf_mit(p, fabrik_mit(uebergang=("Abbrand", "Ausbrand")), {})


def test_takt_bucht_zaehler_in_kwh_und_rechnet_die_einheit_um():
    async def p(app, ha, s):
        s.rollen_setzen({"abgas": "sensor.test_abgas", "waermezaehler": "sensor.wz", "t_aussen": "sensor.aussen"}, True)
        s.profil_setzen({"ofen": "wassergefuehrt", "holz": HOLZ, "erzeuger": [
            {"art": "erdgas", "anteil": 100, "preis": 0.1, "preis_einheit": "kWh", "zaehler": "sensor.gas", "zaehler_einheit": "m³",
             "heizwert": 10.0, "zustandszahl": 1.0}]})
        ha.setzen("sensor.wz", "1000000", {"unit_of_measurement": "Wh"})
        ha.setzen("sensor.gas", "50", {"unit_of_measurement": "m³"})
        ha.setzen("sensor.aussen", "5", {"unit_of_measurement": "°C"})

        async def takt(zeit):
            app._uhr = lambda: zeit
            await app._wechseln()
            await app._ausfuehren(TICK, (PARITAET,))

        await takt(1_800_000_000.0)
        zeile = next(iter(app.buchfuehrung.tage.values()))
        assert zeile["waerme_zaehler_kwh"] is None and zeile["verbrauch_kwh"] is None      # erster Stand: nur der Bezug
        ha.setzen("sensor.wz", "1002.0", {"unit_of_measurement": "kWh"})                    # dieselbe Wärme, andere Einheit
        ha.setzen("sensor.gas", "52", {"unit_of_measurement": "m³"})
        await takt(1_800_000_030.0)
        zeile = next(iter(app.buchfuehrung.tage.values()))
        assert zeile["waerme_zaehler_kwh"] == pytest.approx(2.0) and zeile["verbrauch_kwh"] == pytest.approx(20.0)   # 2 m³ × 10 kWh
        ha.setzen("sensor.wz", "1003.0", {"unit_of_measurement": "MWh"})                    # 1003 MWh wäre ein Sprung nach oben ...
        await takt(1_800_000_060.0)
        assert zeile["waerme_zaehler_kwh"] > 1_000_000                                       # ... und wird als Zuwachs gebucht
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_unbekannte_einheit_des_waermezaehlers_bucht_nichts():
    async def p(app, ha, s):
        s.rollen_setzen({"abgas": "sensor.test_abgas", "waermezaehler": "sensor.wz"}, True)
        s.profil_setzen({"ofen": "wassergefuehrt"})
        ha.setzen("sensor.wz", "10", {"unit_of_measurement": "J"})
        for zeit, wert in ((1_800_000_000.0, "10"), (1_800_000_030.0, "20")):
            ha.setzen("sensor.wz", wert, {"unit_of_measurement": "J"})
            app._uhr = lambda zeit=zeit: zeit
            await app._wechseln()
            await app._ausfuehren(TICK, (PARITAET,))
        assert next(iter(app.buchfuehrung.tage.values()))["waerme_zaehler_kwh"] is None
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_sensor_amortisation_wird_geschrieben():
    async def p(app, ha, s):
        einrichten(s)
        await lauf(app, (PARITAET,))
        assert ha.geschrieben("sensor.kamin_app_amortisation")[-1] == "unknown"      # ohne Investition kein Prozentwert
        app.buchfuehrung.einstellungen_setzen({"investition": 1000})
        app.buchfuehrung.saldo_setzen({"einsparung_eur": 300, "holz_eur": 100, "kaminwaerme_kwh": 100})
        await lauf(app, (PARITAET,))
        assert ha.geschrieben("sensor.kamin_app_amortisation")[-1] == "20.0"
        attribute = [k for m, pfad, k in ha.rest_anfragen
                     if m == "POST" and pfad.endswith("/states/sensor.kamin_app_amortisation")][-1]["attributes"]
        assert attribute["status"] in ("prognose", "zu_wenig_daten") and attribute["netto"] == 200 and attribute["unit_of_measurement"] == "%"
    ablauf_mit(p, fabrik_mit(), {})
