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


WASSERROLLEN = ("t_wt", "t_wt_anstieg", "vorlauf", "ruecklauf", "waermezaehler", "pumpe", "pumpe_stellsignal",
                "puffer_unten", "puffer_oben")


def test_luftgefuehrt_zeigt_keine_wasserrollen():
    p = profil_pruefen({"ofen": LUFT})
    assert not any(rolle_passt(r, p) for r in WASSERROLLEN)
    assert all(rolle_passt(r, p) for r in ("abgas", "druck", "energiezaehler", "t_aussen"))


def test_wassergefuehrt_zeigt_nur_was_da_ist():
    voll = profil()
    assert all(rolle_passt(r, voll) for r in WASSERROLLEN)
    ohne = profil(puffer=False, pumpe_extern=False)
    assert not rolle_passt("puffer_unten", ohne) and rolle_passt("vorlauf", ohne)
    # pumpe_extern ist nur ein Info-Haken: Beide Pumpenrollen hängen an der Wasserseite.
    assert rolle_passt("pumpe", ohne) and rolle_passt("pumpe_stellsignal", ohne)


def test_hinweis_pumpe_nur_ohne_ein_aus_und_ohne_stellsignal():
    p = profil()
    assert "Pumpe ist nicht zugewiesen" in texte(p)
    assert "Pumpe ist nicht zugewiesen" not in texte(p, {"pumpe": "binary_sensor.p"})
    assert "Pumpe ist nicht zugewiesen" not in texte(p, {"pumpe_stellsignal": "sensor.pwm"})


def test_pumpe_laeuft_ab_vorgabe_eigener_wert_und_grenzen():
    assert profil().pumpe_laeuft_ab == 5.0
    assert profil(pumpe_laeuft_ab="").pumpe_laeuft_ab == 5.0
    assert profil(pumpe_laeuft_ab=0).pumpe_laeuft_ab == 0.0
    assert profil(pumpe_laeuft_ab=12.5).pumpe_laeuft_ab == 12.5
    assert profil_pruefen(profil(pumpe_laeuft_ab=20).als_dict()).pumpe_laeuft_ab == 20
    for falsch in (-1, 101, "abc", True):
        with pytest.raises(ProfilFehler, match="Pumpe läuft ab"):
            profil(pumpe_laeuft_ab=falsch)


@pytest.mark.parametrize("zugewiesen,ein_aus,stellsignal,erwartet", [
    (True, "on", 0.0, True),          # Ein/Aus hat Vorrang vor dem Stellsignal
    (True, "off", 80.0, False),
    (True, "unavailable", 80.0, None),
    (False, None, 1.2, False),        # 1,2 % liegt unter der Schwelle 5 %
    (False, None, 5.0, True),
    (False, None, 60.0, True),
    (False, None, None, None),        # beide fehlen
])
def test_pumpe_laeuft_ableitung(zugewiesen, ein_aus, stellsignal, erwartet):
    from anbindung.anlagenprofil import pumpe_laeuft
    assert pumpe_laeuft(zugewiesen, ein_aus, stellsignal, 5.0) is erwartet


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


def test_zaehler_am_erzeuger_ersetzt_die_rolle_energiezaehler():
    p = profil(erzeuger=[{"art": "erdgas", "preis": 0.1, "zaehler": "sensor.gas", "zaehler_einheit": "kWh"}])
    assert "Energiezähler" not in texte(p)
    assert "Energiezähler" in texte(profil(erzeuger=[{"art": "erdgas", "preis": 0.1}]))


def test_luftgefuehrt_fragt_nicht_nach_wasser():
    assert not [h for h in fehlende_angaben(profil_pruefen({"ofen": LUFT}), {}) if h["bereich"] == "Wasser"]


def test_vollstaendig_ist_nichts_zu_melden():
    rollen = {r: "sensor.x" for r in ("abgas", "energiezaehler", "t_aussen", "t_wt", "vorlauf", "ruecklauf", "pumpe", "puffer_unten", "waermezaehler")}
    p = profil(erzeuger=[{"art": "solarthermie"}], holz={"preis": 100, "preis_einheit": "rm"})
    assert fehlende_angaben(p, rollen) == []


# ---- Rückmeldung der Klappe ------------------------------------------------

from anbindung.anlagenprofil import rueck_kennlinie, rueckmeldung_prozent  # noqa: E402


@pytest.mark.parametrize("roh,einheit,erwartet", [
    (6.0, "V", 50.0), (2.0, "V", 0.0), (10.0, "V", 100.0), (1.0, "V", 0.0), (11.0, "V", 100.0),
    (12.0, "mA", 50.0), (40.0, "%", 40.0), (40.0, None, 40.0), (40.0, "", 40.0), (6.0, " v ", 50.0),
])
def test_rueckmeldung_vorgabe_je_einheit(roh, einheit, erwartet):
    assert rueckmeldung_prozent(roh, einheit, profil()) == pytest.approx(erwartet)


def test_rueckmeldung_eigene_werte_wie_hilfsfuehler_der_automation():
    p = profil(rueck_zu=1.87, rueck_auf=10.01)
    assert rueckmeldung_prozent(5.79, "V", p) == pytest.approx(48.157, abs=0.01)
    assert rueck_kennlinie(p, "V") == (1.87, 10.01)


def test_rueckmeldung_invertiert_und_ein_feld_leer():
    assert rueckmeldung_prozent(2.0, "V", profil(rueck_zu=10, rueck_auf=2)) == pytest.approx(100.0)
    # Nur auf eingetragen: zu kommt aus der Vorgabe der Einheit.
    assert rueckmeldung_prozent(6.0, "V", profil(rueck_auf=8)) == pytest.approx(66.667, abs=0.01)


def test_rueckmeldung_unbekannte_einheit_braucht_eigene_werte():
    assert rueckmeldung_prozent(500.0, "mV", profil()) is None
    assert rueckmeldung_prozent(500.0, "mV", profil(rueck_zu=0, rueck_auf=1000)) == pytest.approx(50.0)
    assert rueckmeldung_prozent(None, "V", profil()) is None


def test_rueckmeldung_pruefung():
    with pytest.raises(ProfilFehler, match="mindestens 1"):
        profil(rueck_zu=5, rueck_auf=5.5)
    with pytest.raises(ProfilFehler, match="Rückmeldung bei zu"):
        profil(rueck_zu="x")
    alt = profil()
    assert alt.rueck_zu is None and alt.rueck_auf is None
    assert profil_pruefen(profil(rueck_zu=1.87, rueck_auf=10.01).als_dict()).rueck_auf == 10.01
