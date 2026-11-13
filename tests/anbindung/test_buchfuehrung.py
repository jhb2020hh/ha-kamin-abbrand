"""Buchführung: Auflagen, Zähler, Tageswechsel, Tagespreis und Neustart. Preise und Zähler sind Testwerte."""
import json
from dataclasses import replace
from datetime import datetime

import pytest

from anbindung.anlagenprofil import Anlagenprofil, Erzeuger, Holz
from anbindung.buchfuehrung import Buchfuehrung, Messung
from anbindung.einstellungen import EinstellungsFehler

HOLZ = Holz(preis=0.2, preis_einheit="kg", heizwert=4.0, nutzungsgrad_ofen=0.8, kg_je_auflage=5)
GAS = Erzeuger(art="erdgas", anteil=100, preis=0.10, preis_einheit="kWh", nutzungsgrad=0.9, zaehler="sensor.zaehler_a", zaehler_einheit="kWh")
PROFIL = Anlagenprofil(holz=HOLZ, erzeuger=(GAS,))
EINSTELLUNGEN = {"referenz_a": 144.3, "referenz_b": 7.68, "referenz_sockel": 8, "investition": 5000, "laufende_kosten_jahr": 365}


def zeit(tag, stunde=12, minute=0):
    return datetime(2026, 11, tag, stunde, minute).timestamp()


class Uhr:
    def __init__(self, jetzt):
        self.jetzt = jetzt

    def __call__(self):
        return self.jetzt


class MitUhr(Buchfuehrung):
    """Die Uhr läuft mit den Zeitpunkten der Aufrufe, wie die App mit der Zeit."""

    def __init__(self, pfad, uhr):
        super().__init__(pfad, uhr)
        self.uhr = uhr

    def tick(self, jetzt, *rest):
        self.uhr.jetzt = jetzt
        super().tick(jetzt, *rest)

    def auflage(self, jetzt, *rest):
        self.uhr.jetzt = jetzt
        super().auflage(jetzt, *rest)


@pytest.fixture
def buch(tmp_path):
    uhr = Uhr(zeit(9))
    b = MitUhr(tmp_path / "wirtschaft.json", uhr)
    b.einstellungen_setzen(EINSTELLUNGEN)
    return b


def messung(stand=None, t=None, waerme=None):
    return Messung(waerme, {} if stand is None else {"sensor.zaehler_a": stand}, t)


def tag_fahren(buch, tag, start, ende, t=4.0, profil=PROFIL, waerme=None):
    """Ein ganzer Tag im Minutentakt, der Zähler läuft linear von start bis ende."""
    n = 24 * 60
    for i in range(n + 1):
        jetzt = zeit(tag, 0, 0) + i * 60
        buch.tick(jetzt, messung(start + (ende - start) * i / n, t, None if waerme is None else waerme * i / n), profil)


def zeile(buch, tag, profil=PROFIL):
    return {z["tag"]: z for z in buch.zeilen(profil)}[f"2026-11-{tag:02d}"]


# ---- Auflagen und Tagespreis ----------------------------------------------------------------


def test_auflage_bucht_holz_geld_und_waerme(buch):
    buch.auflage(zeit(9), 1, PROFIL)
    z = zeile(buch, 9)
    assert z["auflagen"] == 1 and z["holz_kg"] == 5 and z["holz_eur"] == pytest.approx(1.0)
    assert z["waerme_holz_kwh"] == pytest.approx(16.0) and z["kaminwaerme_kwh"] == pytest.approx(16.0)


def test_korrektur_nimmt_eine_auflage_zurueck_und_geht_nie_unter_null(buch):
    for _ in range(2):
        buch.auflage(zeit(9), 1, PROFIL)
    buch.auflage(zeit(9), -1, PROFIL)
    z = zeile(buch, 9)
    assert z["auflagen"] == 1 and z["holz_kg"] == pytest.approx(5) and z["holz_eur"] == pytest.approx(1.0)
    buch.auflage(zeit(9), -1, PROFIL)
    buch.auflage(zeit(9), -1, PROFIL)
    z = zeile(buch, 9)
    assert z["auflagen"] == 0 and z["holz_kg"] == 0 and z["holz_eur"] == 0


def test_tagespreis_spaetere_preisaenderung_berührt_die_vergangenheit_nicht(buch):
    buch.auflage(zeit(9), 1, PROFIL)
    teurer = replace(PROFIL, holz=replace(HOLZ, preis=0.4))
    buch.tick(zeit(10, 0, 5), messung(), teurer)           # Tageswechsel schließt den 9. ab
    buch.auflage(zeit(10, 8), 1, teurer)
    assert zeile(buch, 9, teurer)["holz_eur"] == pytest.approx(1.0)
    assert zeile(buch, 10, teurer)["holz_eur"] == pytest.approx(2.0)
    assert zeile(buch, 9, teurer)["final"] is True


def test_geld_der_einsparung_bleibt_nach_preisaenderung_stehen(buch):
    buch.auflage(zeit(9), 1, PROFIL)
    buch.tick(zeit(10, 0, 1), messung(), PROFIL)                 # der 9. ist abgeschlossen
    vorher = zeile(buch, 9)["einsparung_eur"]
    assert vorher == pytest.approx(16.0 * 0.10 / 0.9)
    teuer = replace(PROFIL, erzeuger=(replace(GAS, preis=1.0),))
    buch.tick(zeit(10, 12), messung(), teuer)
    assert zeile(buch, 9, teuer)["einsparung_eur"] == vorher


def test_ohne_gewicht_je_auflage_zaehlt_die_auflage_aber_nicht_das_holz(buch):
    ohne = replace(PROFIL, holz=replace(HOLZ, kg_je_auflage=None))
    buch.auflage(zeit(9), 1, ohne)
    z = zeile(buch, 9, ohne)
    assert z["auflagen"] == 1 and z["holz_kg"] == 0 and z["ohne_menge"] is True


# ---- Zähler -----------------------------------------------------------------------------------


def test_zaehler_bucht_den_zuwachs_zwischen_zwei_takten(buch):
    buch.tick(zeit(9, 10, 0), messung(1000), PROFIL)
    assert zeile(buch, 9)["verbrauch_kwh"] is None            # erster Stand ist nur der Bezug
    buch.tick(zeit(9, 10, 1), messung(1001.5), PROFIL)
    buch.tick(zeit(9, 10, 2), messung(1004.0), PROFIL)
    assert zeile(buch, 9)["verbrauch_kwh"] == pytest.approx(4.0)


def test_zaehlersprung_nach_unten_ist_ein_neuer_bezug_ohne_buchung(buch):
    buch.tick(zeit(9, 10, 0), messung(5000), PROFIL)
    buch.tick(zeit(9, 10, 1), messung(5002), PROFIL)
    buch.tick(zeit(9, 10, 2), messung(3), PROFIL)             # Zähler getauscht oder zurückgesetzt
    assert zeile(buch, 9)["verbrauch_kwh"] == pytest.approx(2.0)
    buch.tick(zeit(9, 10, 3), messung(4.5), PROFIL)
    assert zeile(buch, 9)["verbrauch_kwh"] == pytest.approx(3.5)


def test_unavailable_wird_uebersprungen_und_der_naechste_wert_schliesst_an(buch):
    buch.tick(zeit(9, 10, 0), messung(100), PROFIL)
    buch.tick(zeit(9, 10, 1), messung(None), PROFIL)          # unavailable
    buch.tick(zeit(9, 10, 2), messung(103), PROFIL)
    assert zeile(buch, 9)["verbrauch_kwh"] == pytest.approx(3.0)


def test_lange_luecke_am_selben_tag_wird_gebucht(buch):
    buch.tick(zeit(9, 8, 0), messung(100), PROFIL)
    buch.tick(zeit(9, 10, 0), messung(160), PROFIL)           # zwei Stunden ohne Wert: Der Zähler hat sie gezählt
    assert zeile(buch, 9)["verbrauch_kwh"] == pytest.approx(60.0)


def test_lange_luecke_ueber_mitternacht_beginnt_den_zaehler_neu(buch):
    buch.tick(zeit(9, 22, 0), messung(100), PROFIL)
    buch.tick(zeit(10, 2, 0), messung(160), PROFIL)           # vier Stunden über Mitternacht: keinem Tag zuzuordnen
    assert zeile(buch, 9)["verbrauch_kwh"] is None and zeile(buch, 10)["verbrauch_kwh"] is None
    buch.tick(zeit(10, 2, 1), messung(161), PROFIL)
    assert zeile(buch, 10)["verbrauch_kwh"] == pytest.approx(1.0)


def test_waermezaehler_mit_luecke_ueber_mitternacht_gilt_an_beiden_tagen_nicht_als_messung(buch):
    buch.auflage(zeit(9, 21, 0), 1, PROFIL)
    buch.tick(zeit(9, 21, 59), messung(waerme=50.0), PROFIL)
    buch.tick(zeit(9, 22, 0), messung(waerme=52.0), PROFIL)
    buch.auflage(zeit(10, 2, 0), 1, PROFIL)
    buch.tick(zeit(10, 2, 0), messung(waerme=60.0), PROFIL)   # 8 kWh fehlen beiden Tagen
    buch.tick(zeit(10, 2, 1), messung(waerme=61.0), PROFIL)
    buch.tick(zeit(11, 0, 1), messung(waerme=61.0), PROFIL)
    for tag in (9, 10):
        z = zeile(buch, tag)
        assert z["waerme_luecke"] and z["waerme_art"] == "modell" and z["kaminwaerme_kwh"] == pytest.approx(16.0)


def test_pause_ueber_mitternacht_ohne_zuwachs_ist_keine_luecke(buch):
    buch.tick(zeit(9, 21, 59), messung(waerme=50.0), PROFIL)
    buch.tick(zeit(9, 22, 0), messung(waerme=52.0), PROFIL)
    buch.tick(zeit(10, 2, 0), messung(waerme=52.0), PROFIL)   # Der Ofen war kalt: Es fehlt nichts
    assert not zeile(buch, 9)["waerme_luecke"] and zeile(buch, 9)["waerme_art"] == "gemessen"
    assert not zeile(buch, 10)["waerme_luecke"]


def test_spaet_zugewiesener_waermezaehler_gilt_nicht_als_messung(buch):
    """Wie am 10.11.2026: Der Zähler kam erst abends dazu und sah nur einen Bruchteil des Tages."""
    buch.auflage(zeit(9, 9, 0), 1, PROFIL)
    for minute in range(0, 600):
        buch.tick(zeit(9, 10, 0) + minute * 60, messung(), PROFIL)
    buch.tick(zeit(9, 20, 0), messung(waerme=200.0), PROFIL)
    buch.tick(zeit(9, 20, 1), messung(waerme=201.4), PROFIL)
    z = zeile(buch, 9)
    assert z["waerme_zaehler_kwh"] == pytest.approx(1.4) and z["waerme_s"] == 120
    assert z["waerme_art"] == "modell" and z["kaminwaerme_kwh"] == pytest.approx(16.0)


def test_alte_zeile_ohne_abdeckung_bleibt_gemessen(tmp_path):
    pfad = tmp_path / "wirtschaft.json"
    pfad.write_text(json.dumps({"version": 1, "tage": {"2026-11-08": {"waerme_zaehler_kwh": 10.0, "s": 86400.0}}}), encoding="utf-8")
    b = MitUhr(pfad, Uhr(zeit(9)))
    z = zeile(b, 8)
    assert z["waerme_s"] == 86400.0 and z["waerme_art"] == "gemessen" and z["kaminwaerme_kwh"] == pytest.approx(12.8)


def test_waermezaehler_und_erfasste_zeit(buch):
    buch.tick(zeit(9, 10, 0), Messung(50.0, {}, 3.0), PROFIL)
    buch.tick(zeit(9, 10, 1), Messung(52.0, {}, 5.0), PROFIL)
    z = zeile(buch, 9)
    assert z["waerme_zaehler_kwh"] == pytest.approx(2.0) and z["s"] == 60 and z["t_summe"] / z["t_s"] == pytest.approx(5.0)
    assert z["waerme_art"] == "gemessen" and z["kaminwaerme_kwh"] == pytest.approx(2.0 * 1.28)


def test_langer_abstand_zaehlt_hoechstens_zwei_minuten_als_erfasst(buch):
    buch.tick(zeit(9, 10, 0), messung(1, 4.0), PROFIL)
    buch.tick(zeit(9, 11, 0), messung(1, 4.0), PROFIL)
    assert zeile(buch, 9)["s"] == 120


# ---- Tageswechsel -----------------------------------------------------------------------------


def test_tageswechsel_schliesst_den_vortag_gemessen_ab(buch):
    buch.auflage(zeit(9, 18), 1, PROFIL)
    tag_fahren(buch, 9, 1000.0, 1080.0, t=4.0)
    buch.tick(zeit(10, 0, 30), messung(1080.0, 4.0), PROFIL)
    z9, z10 = zeile(buch, 9), zeile(buch, 10)
    assert z9["final"] and z9["art"] == "gemessen"
    assert z9["verbrauch_kwh"] == pytest.approx(80.0, abs=0.1)       # die erste Minute ist nur der Bezug
    assert z9["einsparung_kwh"] == pytest.approx(113.58 - 80.0, abs=0.2)
    assert z9["einsparung_eur"] == pytest.approx((113.58 - 80.0) * 0.10, abs=0.02)
    assert not z10["final"] and z10["verbrauch_kwh"] < 0.1        # nur die erste Minute des neuen Tages
    assert z9["laufend_eur"] == pytest.approx(1.0)            # 365 € im Jahr


def test_heute_ist_nur_vorlaeufig_geschaetzt(buch):
    buch.auflage(zeit(9, 18), 1, PROFIL)
    buch.tick(zeit(9, 18, 1), messung(1000.0, 4.0), PROFIL)
    z = zeile(buch, 9)
    assert z["art"] == "modell" and not z["final"]


def test_tag_ohne_ganze_abdeckung_bleibt_modell(buch):
    buch.auflage(zeit(9, 9), 1, PROFIL)
    for i in range(0, 600):
        jetzt = zeit(9, 9, 0) + i * 60
        buch.tick(jetzt, messung(1000.0 + i * 0.1, 4.0), PROFIL)
    buch.tick(zeit(10, 0, 30), messung(1060.0, 4.0), PROFIL)
    assert zeile(buch, 9)["art"] == "modell"


def test_app_war_ueber_mitternacht_aus_alte_tage_werden_beim_naechsten_lauf_abgeschlossen(buch):
    buch.auflage(zeit(9, 18), 1, PROFIL)
    buch.tick(zeit(12, 8), messung(), PROFIL)
    assert zeile(buch, 9)["final"] is True


# ---- Neustart -----------------------------------------------------------------------------------


def test_neustart_verliert_weder_auflagen_noch_zaehlerstand(buch, tmp_path):
    buch.auflage(zeit(9, 10), 1, PROFIL)
    buch.tick(zeit(9, 10, 0), messung(1000.0, 4.0), PROFIL)
    buch.sichern(True)
    neu = MitUhr(tmp_path / "wirtschaft.json", buch.uhr)
    neu.tick(zeit(9, 10, 1), messung(1002.5, 4.0), PROFIL)           # App war eine Minute weg
    z = zeile(neu, 9)
    assert z["auflagen"] == 1 and z["verbrauch_kwh"] == pytest.approx(2.5)
    assert neu.einstellungen["investition"] == 5000


def test_gespeichert_wird_atomar_und_lesbar(buch, tmp_path):
    buch.auflage(zeit(9), 1, PROFIL)
    daten = json.loads((tmp_path / "wirtschaft.json").read_text())
    assert daten["tage"]["2026-11-09"]["auflagen"] == 1 and not (tmp_path / "wirtschaft.tmp").exists()


def test_kaputte_datei_wird_nicht_ueberschrieben_sondern_beiseite_gelegt(tmp_path):
    (tmp_path / "wirtschaft.json").write_text("{kaputt")
    b = Buchfuehrung(tmp_path / "wirtschaft.json", Uhr(zeit(9)))
    assert b.tage == {} and (tmp_path / "wirtschaft.defekt").read_text() == "{kaputt"


def test_verwaiste_zaehler_beginnen_bei_erneuter_zuweisung_neu(buch):
    buch.tick(zeit(9, 10, 0), messung(100), PROFIL)
    buch.verwaiste_zaehler_entfernen(set())
    buch.tick(zeit(9, 10, 1), messung(900), PROFIL)
    assert zeile(buch, 9)["verbrauch_kwh"] is None


# ---- Einstellungen, Saldo, Referenz ---------------------------------------------------------------


def test_einstellungen_pruefen_alles_oder_nichts(buch):
    with pytest.raises(EinstellungsFehler):
        buch.einstellungen_setzen({"investition": 100, "referenz_b": -1})
    assert buch.einstellungen["investition"] == 5000
    with pytest.raises(EinstellungsFehler):
        buch.einstellungen_setzen({"gibt_es_nicht": 1})
    with pytest.raises(EinstellungsFehler):
        buch.einstellungen_setzen({"investition": "viel"})
    buch.einstellungen_setzen({"investition": ""})
    assert "investition" not in buch.einstellungen and buch.wert("aufstellraum_anteil") == 0.4


def test_eroeffnungssaldo_geht_in_die_amortisation(buch):
    buch.einstellungen_setzen({"jahreswaerme_kwh": 3000})
    buch.saldo_setzen({"einsparung_eur": 600, "holz_eur": 150, "kaminwaerme_kwh": 900})
    a = buch.amortisation(PROFIL)
    assert a["netto_eur"] == pytest.approx(450) and a["kaminwaerme_kwh"] == 900 and a["status"] == "prognose"
    assert a["prognose_jahre"] == pytest.approx((5000 - 450) / (450 / 900 * 3000 - 365))     # 365 € laufende Kosten im Jahr
    with pytest.raises(EinstellungsFehler):
        buch.saldo_setzen({"holz_eur": -5})
    with pytest.raises(EinstellungsFehler):
        buch.saldo_setzen({"quatsch": 5})
    assert buch.saldo["holz_eur"] == 150


def test_referenz_lernen_aus_eigenen_tagen_ohne_feuer_und_uebernehmen(buch):
    assert not buch.referenz_vorschlag(PROFIL).ok
    stand = 0.0
    for i in range(16):
        t = -2.0 + i
        verbrauch = 120.0 - 6.0 * t
        tag_fahren(buch, 1 + i, stand, stand + verbrauch, t=t)
        stand += verbrauch
    buch.tick(zeit(17, 0, 30), messung(stand, 0.0), PROFIL)
    v = buch.referenz_vorschlag(PROFIL)
    assert v.ok and v.tage == 16 and v.a == pytest.approx(120.0, abs=0.5) and v.b == pytest.approx(6.0, abs=0.05)
    buch.referenz_uebernehmen(PROFIL)
    assert buch.einstellungen["referenz_a"] == pytest.approx(v.a)


def test_referenz_uebernehmen_ohne_vorschlag_ist_ein_fehler(buch):
    with pytest.raises(EinstellungsFehler):
        buch.referenz_uebernehmen(PROFIL)
