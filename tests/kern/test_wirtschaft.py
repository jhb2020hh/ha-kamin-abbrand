"""Kosten je kWh Nutzwärme und Vergleich. Alle Preise stehen im Test, die App belegt keine vor."""
import pytest

from anbindung.anlagenprofil import (LUFT, WASSER, Erzeuger, Holz, Anlagenprofil, fehlende_angaben, profil_pruefen)
from kern import wirtschaft as w


def profil(**werte) -> Anlagenprofil:
    return Anlagenprofil(**werte)


HOLZ = Holz(preis=0.18, preis_einheit="kg", heizwert=4.2, nutzungsgrad_ofen=0.8)


def test_holz_je_kwh_nutzwaerme():
    je_kwh, grund = w.holz_kosten_je_kwh(HOLZ)
    assert grund is None and je_kwh == pytest.approx(0.0536, abs=5e-5)


def test_holz_ohne_eigenen_ofenwirkungsgrad_nimmt_die_vorgabe():
    je_kwh, _ = w.holz_kosten_je_kwh(Holz(preis=0.18, preis_einheit="kg", heizwert=4.2))
    assert je_kwh == pytest.approx(0.18 / 4.2 / 0.8)


def test_holz_je_raummeter_nutzt_das_gewicht():
    je_kwh, _ = w.holz_kosten_je_kwh(Holz(preis=100, preis_einheit="rm", kg_je_rm=400, heizwert=4.0, nutzungsgrad_ofen=0.8))
    assert je_kwh == pytest.approx(100 / (400 * 4.0) / 0.8)


def test_gas_je_kwh_nutzwaerme():
    je_kwh, grund = w.erzeuger_kosten_je_kwh(Erzeuger(art="erdgas", preis=0.12, preis_einheit="kWh", nutzungsgrad=0.9))
    assert grund is None and je_kwh == pytest.approx(0.1333, abs=5e-5)


def test_gas_je_kubikmeter_wird_ueber_brennwert_und_zustandszahl_umgerechnet():
    e = Erzeuger(art="erdgas", preis=1.2, preis_einheit="m³", heizwert=10.0, zustandszahl=1.0, nutzungsgrad=0.9)
    assert w.erzeuger_kosten_je_kwh(e)[0] == pytest.approx(1.2 / 10.0 / 0.9)


def test_oel_pellets_und_waermepumpe_mit_vorgaben():
    assert w.erzeuger_kosten_je_kwh(Erzeuger(art="heizoel", preis=1.0, preis_einheit="l"))[0] == pytest.approx(1.0 / 10.0 / 0.85)
    assert w.erzeuger_kosten_je_kwh(Erzeuger(art="pellets", preis=0.4, preis_einheit="kg"))[0] == pytest.approx(0.4 / 4.8 / 0.85)
    assert w.erzeuger_kosten_je_kwh(Erzeuger(art="waermepumpe", preis=0.3, preis_einheit="kWh", nutzungsgrad=3.5))[0] == pytest.approx(0.3 / 3.5)


def test_solarthermie_ist_kostenlos():
    assert w.erzeuger_kosten_je_kwh(Erzeuger(art="solarthermie")) == (0.0, None)


def test_ohne_preis_fehlt_der_erzeuger_mit_grund():
    assert w.erzeuger_kosten_je_kwh(Erzeuger(art="erdgas")) == (None, "Preis fehlt")
    assert w.holz_kosten_je_kwh(Holz()) == (None, "Preis fehlt")


def test_nutzungsgrad_null_teilt_nicht_durch_null():
    assert w.erzeuger_kosten_je_kwh(Erzeuger(art="erdgas", preis=0.1, preis_einheit="kWh", nutzungsgrad=0.0)) == (None, "Nutzungsgrad fehlt")
    assert w.holz_nutzungsgrad(Holz(nutzungsgrad_ofen=0.0)) == 0.8


def test_vergleich_sortiert_den_guenstigsten_zuerst_und_rechnet_die_differenz():
    p = profil(holz=HOLZ, erzeuger=(Erzeuger(art="erdgas", preis=0.12, preis_einheit="kWh", nutzungsgrad=0.9),
                                    Erzeuger(art="solarthermie")))
    v = w.vergleich(p, 1000)
    assert [z.schluessel for z in v.zeilen] == ["solarthermie", "holz", "erdgas"]
    solar, holz, gas = v.zeilen
    assert solar.kostenlos and solar.kosten == 0 and solar.differenz == pytest.approx(-holz.kosten)
    assert holz.kosten == pytest.approx(53.57, abs=0.01) and holz.differenz == 0
    assert gas.kosten == pytest.approx(133.33, abs=0.01) and gas.differenz == pytest.approx(gas.kosten - holz.kosten)
    assert v.fehlt == ()


def test_vergleich_nennt_was_fehlt_und_laesst_es_weg():
    p = profil(holz=HOLZ, erzeuger=(Erzeuger(art="heizoel"), Erzeuger(art="erdgas", preis=0.1, preis_einheit="kWh")))
    v = w.vergleich(p, 500)
    assert [z.schluessel for z in v.zeilen] == ["holz", "erdgas"]
    assert [(f.schluessel, f.grund) for f in v.fehlt] == [("heizoel", "Preis fehlt")]
    assert v.menge_kwh == 500


def test_vergleich_ohne_holzpreis_hat_keine_differenz():
    v = w.vergleich(profil(erzeuger=(Erzeuger(art="erdgas", preis=0.1, preis_einheit="kWh"),)))
    assert v.zeilen[0].differenz is None
    assert [f.schluessel for f in v.fehlt] == ["holz"]


def test_vergleich_leeres_profil_und_ungueltige_menge():
    v = w.vergleich(profil(), 0)
    assert v.zeilen == () and v.menge_kwh == w.VORGABE_MENGE_KWH and [f.schluessel for f in v.fehlt] == ["holz"]
    assert w.vergleich(profil(holz=HOLZ), -5).menge_kwh == w.VORGABE_MENGE_KWH
    assert w.vergleich(profil(holz=HOLZ), float("nan")).menge_kwh == w.VORGABE_MENGE_KWH


def test_gleiche_art_zweimal_erscheint_zweimal():
    e = Erzeuger(art="erdgas", preis=0.1, preis_einheit="kWh")
    assert len(w.vergleich(profil(erzeuger=(e, e))).zeilen) == 2


# ---- Anteil 0: Erzeuger nur zum Vergleich ----------------------------------------


def _gespeichert(erzeuger):
    return profil_pruefen({"ofen": LUFT, "erzeuger": erzeuger})


def test_erzeuger_mit_anteil_null_ist_nur_zum_vergleich_und_bleibt_gueltig():
    p = _gespeichert([{"art": "erdgas", "anteil": 100, "preis": 0.1}, {"art": "heizoel", "anteil": 0, "preis": 1.0}])
    assert p.erzeuger[1].anteil == 0
    assert "Anteile" not in " ".join(h["text"] for h in fehlende_angaben(p, {}))
    assert {z.schluessel for z in w.vergleich(p).zeilen} == {"erdgas", "heizoel"}


def test_einziger_erzeuger_mit_anteil_null_meldet_nicht_falsche_anteilssumme():
    p = _gespeichert([{"art": "erdgas", "anteil": 0, "preis": 0.1}])
    texte = [h["text"] for h in fehlende_angaben(p, {})]
    assert not any("ergeben 0" in t for t in texte)
    assert any("nur dem Vergleich" in t for t in texte)
    assert [z.schluessel for z in w.vergleich(p).zeilen] == ["erdgas"]


def test_profil_nimmt_ofennutzungsgrad_und_auflage_an_und_prueft_sie():
    p = profil_pruefen({"ofen": WASSER, "holz": {"nutzungsgrad_ofen": 0.75, "kg_je_auflage": 3}})
    assert p.holz.nutzungsgrad_ofen == 0.75 and p.holz.kg_je_auflage == 3
    assert profil_pruefen(p.als_dict()) == p
    with pytest.raises(Exception):
        profil_pruefen({"ofen": LUFT, "holz": {"nutzungsgrad_ofen": 1.5}})


# ---- Nutzwärme, Einheiten, Referenz ------------------------------------------------


def test_nutzwaerme_mit_zaehler_addiert_die_raumwaerme():
    assert w.nutzwaerme_mit_zaehler(100.0) == pytest.approx(128.0)        # 100 + 100 × 0,7 × 0,4
    assert w.nutzwaerme_mit_zaehler(100.0, 1.0, 0.5) == pytest.approx(150.0)


def test_nutzwaerme_aus_holz():
    assert w.nutzwaerme_aus_holz(5, 4.2, 0.8) == pytest.approx(16.8)


@pytest.mark.parametrize("wert, einheit, kwh", [(1500, "Wh", 1.5), (2, "kWh", 2), (0.5, "MWh", 500), (3, " KWH ", 3)])
def test_energie_nach_kwh(wert, einheit, kwh):
    assert w.energie_nach_kwh(wert, einheit) == pytest.approx(kwh)


@pytest.mark.parametrize("einheit", [None, "", "m³", "kW", "J"])
def test_energie_mit_unbekannter_einheit_ist_none(einheit):
    assert w.energie_nach_kwh(5, einheit) is None
    assert w.energie_nach_kwh(None, "kWh") is None


def test_leistung_nach_kw():
    assert w.leistung_nach_kw(4500, "W") == pytest.approx(4.5)
    assert w.leistung_nach_kw(4.5, "kW") == 4.5
    assert w.leistung_nach_kw(4.5, "kWh") is None


def test_referenz_verbrauch_mit_sockel():
    assert w.referenz_kwh(144.3, 7.68, 8, 4) == pytest.approx(113.58)
    assert w.referenz_kwh(144.3, 7.68, 8, 20) == 8       # warm: der Sockel gilt
    assert w.referenz_kwh(144.3, 7.68, None, 30) == 0
    assert w.referenz_kwh(None, 7.68, 8, 4) is None and w.referenz_kwh(144.3, 7.68, 8, None) is None


def _tage(n, a=120.0, b=6.0, t0=-5.0, schritt=1.0):
    return [(t0 + i * schritt, a - b * (t0 + i * schritt)) for i in range(n)]


def test_referenz_lernen_findet_die_gerade():
    v = w.referenz_lernen(_tage(20))
    assert v.ok and v.tage == 20 and v.a == pytest.approx(120.0, abs=0.01) and v.b == pytest.approx(6.0, abs=0.001)


def test_referenz_lernen_braucht_14_tage_und_5_k_spreizung():
    assert not w.referenz_lernen(_tage(13)).ok and "14" in w.referenz_lernen(_tage(13)).grund
    eng = w.referenz_lernen(_tage(20, schritt=0.2))      # 3,8 K Spreizung
    assert not eng.ok and "K" in eng.grund
    assert w.referenz_lernen(_tage(14)).ok and w.referenz_lernen(_tage(6, schritt=1.0) + _tage(8, t0=0)).tage == 14


def test_referenz_lernen_ohne_zusammenhang_ist_kein_vorschlag():
    flach = [(t, 50.0) for t, _ in _tage(20)]
    assert not w.referenz_lernen(flach).ok
    verkehrt = [(t, 50.0 + 2 * t) for t, _ in _tage(20)]
    assert not w.referenz_lernen(verkehrt).ok


# ---- Ein Tag ---------------------------------------------------------------------------


def konfig(**werte):
    p = profil(holz=Holz(preis=0.2, preis_einheit="kg", heizwert=4.0, kg_je_auflage=5, nutzungsgrad_ofen=0.8),
               erzeuger=(Erzeuger(art="erdgas", anteil=100, preis=0.10, preis_einheit="kWh", nutzungsgrad=0.9),))
    return w.konfiguration_aus(p, {"referenz_a": 144.3, "referenz_b": 7.68, "referenz_sockel": 8} | werte)


def zeile(**werte):
    return {"auflagen": 1, "holz_kg": 5.0, "holz_eur": 1.0, "waerme_holz_kwh": 16.0, "waerme_zaehler_kwh": None,
            "verbrauch_kwh": 80.0, "t_summe": 4.0 * 86400, "t_s": 86400.0, "s": 86400.0, "laufend_eur": 0.0,
            "ohne_menge": False, "final": False} | werte


def test_tag_modell_aus_holz_und_erzeugeranteilen():
    e = w.tag_auswerten(zeile(), konfig(), endgueltig=False)
    assert e["art"] == "modell" and e["kaminwaerme_kwh"] == 16.0
    assert e["einsparung_kwh"] == pytest.approx(16.0 / 0.9)
    assert e["einsparung_eur"] == pytest.approx(16.0 * 0.10 / 0.9)


def test_tag_gemessen_nur_am_fertigen_tag():
    k = konfig()
    assert w.tag_auswerten(zeile(), k, endgueltig=False)["art"] == "modell"      # heute: vorläufig
    e = w.tag_auswerten(zeile(), k, endgueltig=True)
    assert e["art"] == "gemessen"
    assert e["einsparung_kwh"] == pytest.approx(113.58 - 80.0)
    assert e["einsparung_eur"] == pytest.approx((113.58 - 80.0) * 0.10)


def test_tag_ohne_volle_abdeckung_oder_referenz_bleibt_modell():
    assert w.tag_auswerten(zeile(s=3600.0), konfig(), True)["art"] == "modell"
    assert w.tag_auswerten(zeile(), konfig(referenz_a=None), True)["art"] == "modell"
    assert w.tag_auswerten(zeile(verbrauch_kwh=None), konfig(), True)["art"] == "modell"
    assert w.tag_auswerten(zeile(t_s=0.0), konfig(), True)["art"] == "modell"


def test_tag_ohne_feuer_hat_keine_einsparung():
    e = w.tag_auswerten(zeile(auflagen=0, waerme_holz_kwh=0.5), konfig(), True)
    assert e["art"] is None and e["einsparung_kwh"] == 0 and e["einsparung_eur"] == 0


def test_tag_mit_waermezaehler_rechnet_nutzwaerme_gemessen():
    e = w.tag_auswerten(zeile(waerme_zaehler_kwh=50.0), konfig(), False)
    assert e["waerme_art"] == "gemessen" and e["kaminwaerme_kwh"] == pytest.approx(64.0)


def test_tag_ohne_preis_hat_keinen_betrag_statt_einer_erfundenen_zahl():
    p = profil(holz=Holz(preis=0.2, preis_einheit="kg", kg_je_auflage=5), erzeuger=(Erzeuger(art="erdgas", anteil=100),))
    k = w.konfiguration_aus(p, {})
    assert w.tag_auswerten(zeile(), k, False)["einsparung_eur"] is None
    assert w.tag_auswerten(zeile(), w.konfiguration_aus(profil(), {}), False)["einsparung_eur"] is None


def test_tag_negative_einsparung_bleibt_sichtbar():
    e = w.tag_auswerten(zeile(verbrauch_kwh=130.0), konfig(), True)
    assert e["einsparung_kwh"] < 0 and e["einsparung_eur"] < 0


# ---- Amortisation -------------------------------------------------------------------------


def tag(kwh, einsparung, holz, laufend=0.0, art="gemessen"):
    return {"kaminwaerme_kwh": kwh, "einsparung_eur": einsparung, "holz_eur": holz, "laufend_eur": laufend, "art": art}


def test_amortisation_ohne_investition_sagt_was_fehlt():
    a = w.amortisation([tag(100, 80, 20)], {}, None, 5000)
    assert a["status"] == w.FEHLT and "Investition" in a["hinweis"] and a["prozent"] is None


def test_amortisation_prognose_erst_ab_50_kwh():
    a = w.amortisation([tag(49, 80, 20)], {}, 1000, 5000)
    assert a["status"] == w.WENIG and a["prognose_jahre"] is None and a["netto_eur"] == 60
    assert w.amortisation([tag(50, 80, 20)], {}, 1000, 5000)["status"] == w.PROGNOSE


def test_amortisation_prognose_rechnet_ist_netto_je_kwh_mal_jahreswaerme():
    a = w.amortisation([tag(100, 80, 20)], {}, 1000, 5000)
    # Netto 60 € bei 100 kWh = 0,60 €/kWh, 5000 kWh im Jahr = 3000 €/Jahr, Rest 940 €.
    assert a["netto_eur"] == 60 and a["rest_eur"] == 940 and a["prozent"] == pytest.approx(6.0)
    assert a["prognose_jahre"] == pytest.approx(940 / 3000)


@pytest.mark.parametrize("einsparung, holz, laufend", [(20, 20, 0), (10, 20, 0), (30, 20, 15)])
def test_amortisation_netto_null_oder_negativ_heisst_keine_amortisation(einsparung, holz, laufend):
    a = w.amortisation([tag(100, einsparung, holz, laufend)], {}, 1000, 5000)
    assert a["status"] == w.KEINE and a["prognose_jahre"] is None and a["netto_eur"] <= 0
    assert a["prozent"] == 0 and "keine Amortisation" in a["hinweis"]


def test_amortisation_zu_wenig_daten_statt_voreiligem_urteil():
    assert w.amortisation([tag(5, 0.5, 1.0, 2.0)], {}, 1000, 5000)["status"] == w.WENIG
    assert w.amortisation([], {}, 1000, 5000)["status"] == w.WENIG


def test_amortisation_ohne_jahreswaerme_keine_prognose():
    a = w.amortisation([tag(100, 80, 20)], {}, 1000, None)
    assert a["status"] == w.WENIG and "Jahreswärme" in a["hinweis"]


def test_amortisation_eingespielt():
    a = w.amortisation([tag(100, 1500, 100)], {}, 1000, 5000)
    assert a["status"] == w.FERTIG and a["rest_eur"] == 0 and a["prozent"] == 100


def test_amortisation_eroeffnungssaldo_zaehlt_mit():
    saldo = {"einsparung_eur": 500, "holz_eur": 100, "kaminwaerme_kwh": 400}
    a = w.amortisation([tag(10, 8, 2)], saldo, 1000, 5000)
    assert a["netto_eur"] == pytest.approx(406) and a["kaminwaerme_kwh"] == 410 and a["status"] == w.PROGNOSE


def test_amortisation_kennzeichnet_gemessen_schaetzung_und_gemischt():
    gem, mod = tag(10, 8, 2, art="gemessen"), tag(10, 8, 2, art="modell")
    assert w.amortisation([gem], {}, 1000, 1)["art"] == "gemessen"
    assert w.amortisation([mod], {}, 1000, 1)["art"] == "schaetzung"
    a = w.amortisation([gem, mod], {}, 1000, 1)
    assert a["art"] == "gemischt" and (a["tage_gemessen"], a["tage_modell"]) == (1, 1)
    assert w.amortisation([], {}, 1000, 1)["art"] == "keine"


def test_amortisation_zaehlt_tage_ohne_betrag():
    assert w.amortisation([tag(100, None, 20)], {}, 1000, 5000)["tage_unvollstaendig"] == 1
