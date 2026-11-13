"""Übertemperatur: Verzögerung, Wiederholung, Entwarnung mit Hysterese, ungültiges T.WT, Push im Lauschen.

Dazu die Pumpe in der App: Ableitung aus Ein/Aus und Stellsignal, Status und Verlauf."""
import pytest

from anbindung.ansicht import status, verlauf_lesen
from anbindung.uebertemperatur import ART, TITEL_ENTWARNUNG, TITEL_WARNUNG, UebertempWaechter, wasser_gueltig
from test_push import ablauf_mit, fabrik_mit, lauf
from testdaten import ENTITAETEN as E

T0 = 1_800_000_000.0
GRENZEN = dict(t_uebertemperatur=400.0, t_wasser_max=85.0, klappe_uebertemperatur=25.0, klappe_wasser=40.0)


def pruefen(w, jetzt, t=None, twt=None, **weiteres):
    return w.pruefen(T0 + jetzt, t_abgas=t, twt_roh=twt, **(GRENZEN | weiteres))


# ---- Wächter --------------------------------------------------------------

def test_abgas_warnt_erst_nach_30_s_mit_wert_und_klappe():
    w = UebertempWaechter()
    assert pruefen(w, 0, t=450) == []
    assert pruefen(w, 29, t=450) == []
    meldungen = pruefen(w, 30, t=455)
    assert meldungen == [(ART, TITEL_WARNUNG, "Abgas 455 °C, Grenze 400 °C. Klappe auf 25 % gedrosselt.")]
    assert w.gewarnt("abgas")


def test_wasser_warnt_erst_nach_60_s_mit_hinweis_auf_pumpe():
    w = UebertempWaechter()
    assert pruefen(w, 0, twt=90) == []
    assert pruefen(w, 59, twt=90) == []
    (art, titel, text), = pruefen(w, 60, twt=90.4)
    assert art == ART and titel == TITEL_WARNUNG
    assert text == "Wasser am Ofen 90.4 °C, Grenze 85 °C. Klappe max 40 %, nicht nachlegen, Pumpe/Puffer prüfen."


def test_verzoegerung_beginnt_neu_wenn_der_wert_zwischendurch_faellt():
    w = UebertempWaechter()
    pruefen(w, 0, t=450)
    pruefen(w, 20, t=390)
    assert pruefen(w, 40, t=450) == []
    assert pruefen(w, 69, t=450) == []
    assert len(pruefen(w, 70, t=450)) == 1


def test_wiederholung_alle_300_s_und_nicht_vorher():
    w = UebertempWaechter()
    pruefen(w, 0, t=450)
    assert len(pruefen(w, 30, t=450)) == 1
    for s in (35, 100, 200, 329):
        assert pruefen(w, s, t=460) == []
    assert len(pruefen(w, 330, t=470)) == 1
    assert pruefen(w, 400, t=470) == []
    assert len(pruefen(w, 630, t=470)) == 1


def test_entwarnung_unter_grenze_minus_10_k_einmal_dann_wieder_scharf():
    w = UebertempWaechter()
    pruefen(w, 0, t=450)
    pruefen(w, 30, t=450)
    assert pruefen(w, 40, t=389) == [(ART, TITEL_ENTWARNUNG, "Abgas wieder normal: 389 °C.")]
    assert not w.gewarnt("abgas")
    assert pruefen(w, 50, t=380) == []
    # Wieder scharf: erneut erst nach der Verzögerung
    assert pruefen(w, 60, t=450) == []
    assert len(pruefen(w, 90, t=450)) == 1


def test_hysterese_zwischen_grenze_minus_10_und_grenze_keine_entwarnung_und_keine_wiederholung():
    w = UebertempWaechter()
    pruefen(w, 0, t=450)
    pruefen(w, 30, t=450)
    for s, t in ((40, 400), (100, 395), (400, 390.5), (800, 391)):
        assert pruefen(w, s, t=t) == []
    assert w.gewarnt("abgas")
    # Wieder über der Grenze: Die Wiederholung zählt ab der letzten Warnung, es gibt keine neue Verzögerung.
    assert len(pruefen(w, 810, t=401)) == 1


def test_wasser_entwarnung_unter_75():
    w = UebertempWaechter()
    pruefen(w, 0, twt=90)
    pruefen(w, 60, twt=90)
    assert pruefen(w, 70, twt=76) == []
    assert pruefen(w, 80, twt=74.5) == [(ART, TITEL_ENTWARNUNG, "Wasser am Ofen wieder normal: 74.5 °C.")]


@pytest.mark.parametrize("roh", [None, 1638.0, -5.0, 120.5, float("nan")])
def test_wasser_ungueltig_meldet_nichts(roh):
    assert wasser_gueltig(roh) is None
    w = UebertempWaechter()
    for s in (0, 60, 120, 400):
        assert pruefen(w, s, twt=roh) == []


def test_ungueltiger_wert_haelt_eine_warnung_ohne_entwarnung_und_unterbricht_die_verzoegerung():
    w = UebertempWaechter()
    pruefen(w, 0, twt=90)
    pruefen(w, 60, twt=90)
    assert pruefen(w, 70, twt=1638) == [] and w.gewarnt("wasser")
    assert pruefen(w, 500, twt=None) == [] and w.gewarnt("wasser")
    w2 = UebertempWaechter()
    pruefen(w2, 0, t=450)
    pruefen(w2, 20, t=None)
    assert pruefen(w2, 30, t=450) == []


def test_abgas_und_wasser_unabhaengig():
    w = UebertempWaechter()
    pruefen(w, 0, t=450, twt=90)
    assert [m[2][:5] for m in pruefen(w, 30, t=450, twt=90)] == ["Abgas"]
    assert [m[2][:6] for m in pruefen(w, 60, t=450, twt=90)] == ["Wasser"]


def test_ohne_drosselung_anderer_hinweis():
    w = UebertempWaechter()
    pruefen(w, 0, t=450, twt=90, gedrosselt=False)
    (_, _, abgas), = pruefen(w, 30, t=450, twt=90, gedrosselt=False)
    (_, _, wasser), = pruefen(w, 60, t=450, twt=90, gedrosselt=False)
    assert "gedrosselt" not in abgas and "Klappe" not in wasser and "Pumpe/Puffer prüfen" in wasser


# ---- In der App -----------------------------------------------------------

UEBER = {"uebertemperatur": ["notify.a"]}


def zeit_steuern(app):
    zeit = [T0]
    app._uhr = lambda: zeit[0]
    app.benachrichtiger._uhr = app._uhr
    return zeit


def test_im_lauschen_push_mit_wiederholung_und_entwarnung():
    async def p(app, ha, s):
        assert not app.im_betrieb
        s.parameter_setzen("t_uebertemperatur", 400)
        s.parameter_setzen("klappe_uebertemperatur", 25)
        zeit = zeit_steuern(app)
        ha.setzen(E.abgas, "450", {"unit_of_measurement": "°C"})
        await app._wechseln()
        for schritt in (0, 30, 60, 330):
            zeit[0] = T0 + schritt
            await lauf(app)
        sendungen = ha.gesendet()
        assert len(sendungen) == 2           # bei 30 s und bei 330 s
        assert sendungen[0][1]["title"] == TITEL_WARNUNG
        assert sendungen[0][1]["message"] == "Abgas 450 °C, Grenze 400 °C. Klappe auf 25 % gedrosselt."
        assert sendungen[0][1]["data"] == {"push": {"interruption-level": "time-sensitive"}}
        ha.setzen(E.abgas, "380", {"unit_of_measurement": "°C"})
        await app._wechseln()
        zeit[0] = T0 + 340
        await lauf(app)
        assert ha.gesendet()[-1][1]["title"] == TITEL_ENTWARNUNG and len(ha.gesendet()) == 3
        assert any("wieder normal" in m["text"] for m in app.meldungen)
    ablauf_mit(p, fabrik_mit(), UEBER)


def test_wasser_im_lauschen_und_t_wt_offen_meldet_nichts():
    async def p(app, ha, s):
        s.rollen_setzen({"abgas": E.abgas, "t_wt": "sensor.twt"}, True)
        s.parameter_setzen("t_wasser_max", 85)
        zeit = zeit_steuern(app)
        ha.setzen("sensor.twt", "1638", {"unit_of_measurement": "°C"})
        await app._wechseln()
        for schritt in (0, 60, 120):
            zeit[0] = T0 + schritt
            await lauf(app)
        assert ha.gesendet() == []
        ha.setzen("sensor.twt", "90", {"unit_of_measurement": "°C"})
        await app._wechseln()
        for schritt in (130, 190):
            zeit[0] = T0 + schritt
            await lauf(app)
        (_, inhalt), = ha.gesendet()
        assert inhalt["message"].startswith("Wasser am Ofen 90.0 °C, Grenze 85 °C.")
    ablauf_mit(p, fabrik_mit(), UEBER, wasser=True)


def test_ohne_zuordnung_wird_nichts_gesendet():
    async def p(app, ha, s):
        zeit = zeit_steuern(app)
        ha.setzen(E.abgas, "900", {"unit_of_measurement": "°C"})
        await app._wechseln()
        for schritt in (0, 30):
            zeit[0] = T0 + schritt
            await lauf(app)
        assert ha.gesendet() == [] and app.uebertemperatur.gewarnt("abgas")
    ablauf_mit(p, fabrik_mit(), {})


# ---- Pumpe in der App -----------------------------------------------------

PWM = "sensor.kamin_pumpe_pwm"


async def pumpe_einrichten(app, s, rollen):
    s.rollen_setzen({"abgas": E.abgas} | rollen, True)
    s.profil_setzen({"ofen": "wassergefuehrt", "pumpe_laeuft_ab": 5})
    await app._wechseln()


def test_pumpe_aus_dem_stellsignal_mit_schwelle():
    async def p(app, ha, s):
        ha.setzen(PWM, "1.2", {"unit_of_measurement": "%"})
        await pumpe_einrichten(app, s, {"pumpe_stellsignal": PWM})
        assert app.pumpe_zugewiesen() and "pumpe" in app._wasser_messwerte(None).zugewiesen
        st = status(app)
        assert st["pumpe"] is False and st["pumpe_stellsignal"] == 1.2
        assert app._anzeige()["pumpe"] == 1.2
        ha.setzen(PWM, "60", {"unit_of_measurement": "%"})
        await app._wechseln()
        assert status(app)["pumpe"] is True and app._wasser_messwerte(None).pumpe is True
        await lauf(app)
        d = verlauf_lesen(app.verlauf._ordner, 1, app._uhr() + 1)
        assert d["pumpe"][-1] == 60
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_pumpe_ein_aus_hat_vorrang_und_ohne_beides_none():
    async def p(app, ha, s):
        ha.setzen(PWM, "60", {"unit_of_measurement": "%"})
        ha.setzen("binary_sensor.pumpe", "off")
        await pumpe_einrichten(app, s, {"pumpe_stellsignal": PWM, "pumpe": "binary_sensor.pumpe"})
        st = status(app)
        assert st["pumpe"] is False and st["pumpe_stellsignal"] == 60
        await pumpe_einrichten(app, s, {"pumpe": "binary_sensor.pumpe"})
        assert status(app)["pumpe_stellsignal"] is None and app._anzeige()["pumpe"] == 0.0
        await pumpe_einrichten(app, s, {})
        st = status(app)
        assert st["pumpe"] is None and st["pumpe_stellsignal"] is None and app._anzeige()["pumpe"] is None
        assert not app.pumpe_zugewiesen() and "pumpe" not in app._wasser_messwerte(None).zugewiesen
    ablauf_mit(p, fabrik_mit(), {}, wasser=True)


def test_pumpe_bei_luftgefuehrt_nicht_gelesen():
    async def p(app, ha, s):
        ha.setzen(PWM, "60", {"unit_of_measurement": "%"})
        s.rollen_setzen({"abgas": E.abgas, "pumpe_stellsignal": PWM}, True)
        await app._wechseln()
        st = status(app)
        assert st["pumpe"] is None and st["pumpe_stellsignal"] is None
    ablauf_mit(p, fabrik_mit(), {})
