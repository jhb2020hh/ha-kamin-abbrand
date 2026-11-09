"""Anlagenprofil: Prüfung, Umrechnung, was zum Profil passt und was fehlt."""
import pytest

from anbindung.anlagenprofil import (ARTEN, LUFT, WASSER, Erzeuger, Holz, ProfilFehler, anteile, fehlende_angaben,
                                     feld_passt, kwh_je_einheit, preis_je_kwh, profil_aus_bestand, profil_pruefen, rolle_passt,
                                     temperatur_nach_c, wirksamer_nutzungsgrad, zaehler_entitaeten, zaehler_nach_kwh)


def profil(**werte):
    return profil_pruefen({"ofen": WASSER, "puffer": True, "pumpe_extern": True} | werte)


def texte(p, rollen=None):
    return " | ".join(h["text"] for h in fehlende_angaben(p, rollen or {}))


# ---- Prüfung -------------------------------------------------------------


def test_als_dict_und_zurueck_ergibt_dasselbe():
    p = profil(waehrung="CHF", klappe_motorisiert=False, holz={"preis": 95, "preis_einheit": "rm", "kg_je_rm": 450},
               erzeuger=[{"art": "erdgas", "anteil": 70, "preis": 1.1, "preis_einheit": "m³", "heizwert": 11, "zustandszahl": 0.96,
                          "zaehler": "sensor.gas", "zaehler_einheit": "m³"},
                         {"art": "waermepumpe", "anteil": 30, "preis": 0.3, "preis_einheit": "kWh", "nutzungsgrad": 3.6}])
    assert profil_pruefen(p.als_dict()) == p
    assert p.waehrung == "CHF" and p.klappe_motorisiert is False and len(p.erzeuger) == 2


@pytest.mark.parametrize("art", list(ARTEN))
def test_alle_arten_gehen_ohne_preis(art):
    e = profil(erzeuger=[{"art": art}]).erzeuger[0]
    assert e.art == art and e.preis is None


def test_beliebig_viele_und_gleiche_erzeuger():
    assert len(profil(erzeuger=[{"art": "pellets"}] * 25).erzeuger) == 25


def test_keine_preise_vorbelegt():
    p = profil(erzeuger=[{"art": a} for a in ARTEN])
    assert all(e.preis is None for e in p.erzeuger) and p.holz.preis is None


def test_luftgefuehrt_hat_weder_puffer_noch_pumpe():
    p = profil_pruefen({"ofen": LUFT, "puffer": True, "pumpe_extern": True})
    assert p.puffer is False and p.pumpe_extern is False


def test_solarthermie_nimmt_keinen_preis_an():
    e = profil(erzeuger=[{"art": "solarthermie", "preis": 5, "nutzungsgrad": 0.5}]).erzeuger[0]
    assert e.preis is None and e.preis_einheit is None and e.nutzungsgrad is None


def test_zaehler_einheit_wird_normiert_und_ohne_zaehler_verworfen():
    a, b = profil(erzeuger=[{"art": "erdgas", "zaehler": "sensor.g", "zaehler_einheit": "m3"},
                            {"art": "erdgas", "zaehler_einheit": "m³"}]).erzeuger
    assert a.zaehler_einheit == "m³" and b.zaehler_einheit is None


@pytest.mark.parametrize("eingabe, text", [
    (None, "ungültig"), ({"ofen": "holz"}, "Ofenart"), ({}, "Ofenart"),
    ({"ofen": LUFT, "erzeuger": "erdgas"}, "ungültig"),
    ({"ofen": LUFT, "erzeuger": ["erdgas"]}, "ungültig"),
    ({"ofen": LUFT, "erzeuger": [{"art": "kohle"}]}, "Unbekannt"),
    ({"ofen": LUFT, "erzeuger": [{"art": ["erdgas"]}]}, "Unbekannt"),
    ({"ofen": LUFT, "puffer": "ja", "tuerkontakt": "ja"}, "ja oder nein"),
    ({"ofen": LUFT, "waehrung": "Euronen"}, "Währung"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "anteil": 101}]}, "Anteil"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "preis": -1}]}, "Preis"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "preis": True}]}, "Zahl"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "preis": "abc"}]}, "Zahl"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "preis": float("nan")}]}, "Zahl"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "preis": 1, "preis_einheit": "kg"}]}, "Erlaubt sind"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "nutzungsgrad": 90}]}, "Nutzungsgrad"),
    ({"ofen": LUFT, "erzeuger": [{"art": "waermepumpe", "nutzungsgrad": 0.5}]}, "JAZ"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "zustandszahl": 2}]}, "Zustandszahl"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "zaehler": "kein punkt", "zaehler_einheit": "m³"}]}, "gültige"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "zaehler": "switch.a", "zaehler_einheit": "m³"}]}, "erlaubt"),
    ({"ofen": LUFT, "erzeuger": [{"art": "erdgas", "zaehler": "sensor.a"}]}, "Einheit des Zählers"),
    ({"ofen": LUFT, "erzeuger": [{"art": "strom", "zaehler": "sensor.a", "zaehler_einheit": "m³"}]}, "Erlaubt sind"),
    ({"ofen": LUFT, "holz": {"preis": 1, "preis_einheit": "l"}}, "Erlaubt sind"),
])
def test_ungueltiges_wird_mit_grund_abgelehnt(eingabe, text):
    with pytest.raises(ProfilFehler, match=text):
        profil_pruefen(eingabe)


def test_bestand_mit_wasserrollen_wird_wassergefuehrt():
    p = profil_aus_bestand({"abgas": "sensor.a", "t_wt": "sensor.w", "puffer_unten": "sensor.p"}, False)
    assert p.wassergefuehrt and p.puffer and p.pumpe_extern and p.tuerkontakt is False
    assert not profil_aus_bestand({"abgas": "sensor.a"}).wassergefuehrt


# ---- Umrechnung ----------------------------------------------------------


def test_gas_nutzt_brennwert_und_zustandszahl():
    e = Erzeuger("erdgas", heizwert=11.0, zustandszahl=0.96, zaehler="sensor.g", zaehler_einheit="m³")
    assert zaehler_nach_kwh(e, 100.0) == pytest.approx(1056.0)
    assert kwh_je_einheit(Erzeuger("erdgas"), "m³") == pytest.approx(10.5 * 0.95)


@pytest.mark.parametrize("art, einheit, stand, kwh", [
    ("erdgas", "kWh", 12.0, 12.0), ("strom", "MWh", 1.5, 1500.0), ("heizoel", "l", 10.0, 100.0),
    ("heizoel", "m³", 1.0, 10000.0), ("pellets", "kg", 10.0, 48.0),
])
def test_zaehler_nach_kwh(art, einheit, stand, kwh):
    assert zaehler_nach_kwh(Erzeuger(art, zaehler_einheit=einheit), stand) == pytest.approx(kwh)


def test_zaehler_ohne_einheit_oder_wert_gibt_nichts_erfundenes():
    assert zaehler_nach_kwh(Erzeuger("erdgas"), 5.0) is None
    assert zaehler_nach_kwh(Erzeuger("erdgas", zaehler_einheit="m³"), None) is None
    assert zaehler_nach_kwh(Erzeuger("strom", zaehler_einheit="m³"), 5.0) is None


@pytest.mark.parametrize("brennstoff, je_kwh", [
    (Erzeuger("erdgas", preis=0.12, preis_einheit="kWh"), 0.12),
    (Erzeuger("erdgas", preis=1.0, preis_einheit="m³", heizwert=10.0, zustandszahl=1.0), 0.1),
    (Erzeuger("heizoel", preis=1.0, preis_einheit="l"), 0.1),
    (Erzeuger("heizoel", preis=1000.0, preis_einheit="m³"), 0.1),
    (Erzeuger("pellets", preis=0.48, preis_einheit="kg"), 0.1),
    (Erzeuger("pellets", preis=480.0, preis_einheit="t"), 0.1),
    (Erzeuger("hackschnitzel", preis=80.0, preis_einheit="srm"), 0.1),
    (Erzeuger("waermepumpe", preis=0.3, preis_einheit="kWh"), 0.3),
    (Holz(preis=0.4, preis_einheit="kg"), 0.1),
    (Holz(preis=400.0, preis_einheit="t"), 0.1),
    (Holz(preis=160.0, preis_einheit="rm"), 0.1),
    (Holz(preis=112.0, preis_einheit="srm"), 0.1),
    (Holz(preis=200.0, preis_einheit="rm", heizwert=5.0, kg_je_rm=400.0), 0.1),
])
def test_preis_je_kwh(brennstoff, je_kwh):
    assert preis_je_kwh(brennstoff) == pytest.approx(je_kwh)


def test_ohne_preis_kein_ergebnis_solarthermie_kostenlos():
    assert preis_je_kwh(Erzeuger("erdgas")) is None and preis_je_kwh(Holz()) is None
    assert preis_je_kwh(Erzeuger("solarthermie")) == 0.0


def test_preis_null_ist_ein_preis():
    assert preis_je_kwh(profil(erzeuger=[{"art": "strom", "preis": 0}]).erzeuger[0]) == 0.0


def test_nutzungsgrad_eigener_wert_vor_vorgabe():
    assert wirksamer_nutzungsgrad(Erzeuger("waermepumpe")) == 3.0
    assert wirksamer_nutzungsgrad(Erzeuger("erdgas", nutzungsgrad=0.7)) == 0.7
    assert wirksamer_nutzungsgrad(Erzeuger("solarthermie")) is None


@pytest.mark.parametrize("wert, einheit, celsius", [
    (212.0, "°F", 100.0), (32.0, "℉", 0.0), (100.0, "°C", 100.0), (100.0, None, 100.0), (100.0, "K", 100.0),
])
def test_temperatur(wert, einheit, celsius):
    assert temperatur_nach_c(wert, einheit) == pytest.approx(celsius)


def test_celsius_bleibt_auf_das_bit_gleich():
    assert temperatur_nach_c(213.37, "°C") == 213.37


@pytest.mark.parametrize("liste, erwartet", [
    ([{"art": "erdgas"}], (1.0,)),
    ([{"art": "erdgas", "anteil": 70}, {"art": "strom", "anteil": 30}], (0.7, 0.3)),
    ([{"art": "erdgas", "anteil": 33.3}, {"art": "strom", "anteil": 33.3}, {"art": "pellets", "anteil": 33.3}], (1 / 3, 1 / 3, 1 / 3)),
    ([{"art": "erdgas", "anteil": 50}, {"art": "strom", "anteil": 30}], None),
    ([{"art": "erdgas"}, {"art": "strom"}], None),
    ([{"art": "erdgas", "anteil": 60}], None),
    ([], None),
])
def test_anteile(liste, erwartet):
    ergebnis = anteile(profil(erzeuger=liste))
    assert ergebnis == (None if erwartet is None else pytest.approx(erwartet))


# ---- Was zum Profil passt ------------------------------------------------


WASSERROLLEN = ("t_wt", "t_wt_anstieg", "vorlauf", "ruecklauf", "waermezaehler", "pumpe", "puffer_unten", "puffer_oben")


def test_luftgefuehrt_zeigt_keine_wasserrollen():
    p = profil_pruefen({"ofen": LUFT})
    assert not any(rolle_passt(r, p) for r in WASSERROLLEN)
    assert all(rolle_passt(r, p) for r in ("abgas", "druck", "energiezaehler", "t_aussen"))


def test_wassergefuehrt_zeigt_nur_was_da_ist():
    voll = profil()
    assert all(rolle_passt(r, voll) for r in WASSERROLLEN)
    ohne = profil(puffer=False, pumpe_extern=False)
    assert not rolle_passt("puffer_unten", ohne) and not rolle_passt("pumpe", ohne) and rolle_passt("vorlauf", ohne)


def test_ohne_motor_und_ohne_tuerkontakt():
    p = profil(klappe_motorisiert=False, tuerkontakt=False)
    assert not any(rolle_passt(r, p) for r in ("klappe_ist", "ausgang_klappe", "ausgang_heartbeat", "handbetrieb", "wartung", "tuer"))


def test_einstellfelder_nach_profil():
    luft = profil_pruefen({"ofen": LUFT, "klappe_motorisiert": False})
    assert not any(feld_passt(n, luft) for n in ("t_wasser_max", "klappe_wasser", "puffer_voll", "klappe_glut"))
    assert feld_passt("t_ziel", luft) and all(feld_passt(n, profil()) for n in ("t_wasser_max", "puffer_voll", "klappe_glut"))


def test_zaehler_entitaeten():
    p = profil(erzeuger=[{"art": "erdgas", "zaehler": "sensor.g", "zaehler_einheit": "m³"}, {"art": "strom"}])
    assert zaehler_entitaeten(p) == ["sensor.g"]


# ---- Was fehlt -----------------------------------------------------------


def test_fehlendes_wird_benannt():
    t = texte(profil(erzeuger=[{"art": "erdgas", "anteil": 50}, {"art": "strom", "anteil": 30, "preis": 0.3}]))
    assert "Abgastemperatur" in t and "Erdgas: Preis fehlt" in t and "Strom direkt: Preis" not in t
    assert "ergeben 80 %" in t and "Brennholz" in t and "Energiezähler" in t and "Rücklauf" in t


def test_luftgefuehrt_fragt_nicht_nach_wasser():
    assert not [h for h in fehlende_angaben(profil_pruefen({"ofen": LUFT}), {}) if h["bereich"] == "Wasser"]


def test_vollstaendig_ist_nichts_zu_melden():
    rollen = {r: "sensor.x" for r in ("abgas", "energiezaehler", "t_aussen", "t_wt", "vorlauf", "ruecklauf", "pumpe", "puffer_unten", "waermezaehler")}
    p = profil(erzeuger=[{"art": "solarthermie"}], holz={"preis": 100, "preis_einheit": "rm"})
    assert fehlende_angaben(p, rollen) == []
