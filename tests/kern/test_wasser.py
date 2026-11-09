"""Wasserseite: Entprellung, Hysterese, Fühlerfehler und Regeln nur mit zugewiesenem Fühler."""
import pytest

from kern.wasser import ALARM, HINWEIS, OK, WARNUNG, Grenzen, Messwerte, Wasserwache

ALLE = frozenset({"t_wt", "vorlauf", "ruecklauf", "puffer_unten", "pumpe"})
G = Grenzen(t_wasser_max=85.0, puffer_voll=60.0)


def messung(**werte):
    ruhig = {"zugewiesen": ALLE, "phase": "Abbrand", "pumpe": True, "t_wt": 60.0, "vorlauf": 70.0, "ruecklauf": 60.0,
             "puffer_unten": 40.0}
    return Messwerte(**(ruhig | werte))


def verlauf(wache, werte, von=0.0, bis=600.0, schritt=30.0, grenzen=G):
    """Gleiche Messung im 30-s-Takt. Gibt das letzte Ergebnis zurück."""
    t, erg = von, None
    while t <= bis:
        erg = wache.pruefen(t, werte, grenzen)
        t += schritt
    return erg


def schluessel(erg):
    return [b.schluessel for b in erg.befunde]


def test_ohne_auffaelligkeit_ist_alles_ok():
    erg = verlauf(Wasserwache(), messung(t_wt=70, vorlauf=75, ruecklauf=60, puffer_unten=40))
    assert erg.stufe == OK and erg.befunde == ()


# ---- Jede Regel nur mit zugewiesenem Fühler ---------------------------------------------------


@pytest.mark.parametrize("fehlt, werte", [
    ("ruecklauf", {"ruecklauf": 30}), ("pumpe", {"ruecklauf": 30}),
    ("vorlauf", {"vorlauf": 99}), ("t_wt", {"t_wt": 99}),
    ("pumpe", {"t_wt": 75, "pumpe": False}), ("puffer_unten", {"puffer_unten": 90}),
])
def test_regel_ohne_zugewiesenen_fuehler_laeuft_nicht(fehlt, werte):
    zugewiesen = ALLE - {fehlt}
    if fehlt == "pumpe":
        werte = werte | {"pumpe": None}
    erg = verlauf(Wasserwache(), messung(zugewiesen=zugewiesen, **werte), bis=1200)
    assert erg.stufe == OK, schluessel(erg)


def test_ohne_jeden_fuehler_bleibt_es_ok_auch_bei_unsinnswerten():
    erg = verlauf(Wasserwache(), Messwerte(phase="Abbrand", t_wt=999, vorlauf=999, pumpe=False), bis=1200)
    assert erg.stufe == OK


def test_fuehler_wird_nach_dem_entfernen_nicht_mit_altem_zustand_weiterbenutzt():
    w = Wasserwache()
    assert verlauf(w, messung(t_wt=90), bis=60).stufe == ALARM
    assert w.pruefen(100, messung(zugewiesen=ALLE - {"t_wt"}, t_wt=90), G).stufe == OK
    assert w.pruefen(110, messung(t_wt=90), G).stufe == OK        # beginnt von vorn, erst die Entprellung abwarten
    assert w.pruefen(150, messung(t_wt=90), G).stufe == ALARM


# ---- Wasser am Ofen ----------------------------------------------------------------------------


def test_twt_alarm_ab_der_grenze_nach_der_entprellung():
    w = Wasserwache()
    assert w.pruefen(0, messung(t_wt=85), G).stufe == OK           # erst angeschlagen
    erg = w.pruefen(30, messung(t_wt=85), G)
    assert erg.stufe == ALARM and schluessel(erg) == ["twt_zu_heiss"]
    assert "85" in erg.befunde[0].text


def test_twt_kurze_spitze_loest_nichts_aus():
    w = Wasserwache()
    w.pruefen(0, messung(t_wt=90), G)
    assert w.pruefen(15, messung(t_wt=80), G).stufe == OK
    assert w.pruefen(30, messung(t_wt=90), G).stufe == OK


def test_twt_hysterese_haelt_den_alarm_bis_zwei_kelvin_darunter():
    w = Wasserwache()
    verlauf(w, messung(t_wt=86), bis=60)
    assert w.pruefen(100, messung(t_wt=84.5), G).stufe == ALARM
    assert w.pruefen(110, messung(t_wt=83.0), G).stufe == ALARM    # genau Grenze − 2 K: noch gehalten
    assert w.pruefen(120, messung(t_wt=82.9), G).stufe == OK


def test_twt_grenze_kommt_aus_den_parametern():
    assert verlauf(Wasserwache(), messung(t_wt=76), bis=60, grenzen=Grenzen(t_wasser_max=75)).stufe == ALARM


# ---- Pumpe steht bei Feuer -----------------------------------------------------------------------


def test_pumpe_steht_bei_heissem_wasser_und_feuer_ist_alarm_nach_zwei_minuten():
    w = Wasserwache()
    assert verlauf(w, messung(t_wt=72, pumpe=False), bis=90).stufe == OK
    erg = w.pruefen(120, messung(t_wt=72, pumpe=False), G)
    assert erg.stufe == ALARM and schluessel(erg) == ["pumpe_steht"]


@pytest.mark.parametrize("werte", [
    {"t_wt": 72, "pumpe": True}, {"t_wt": 69, "pumpe": False}, {"t_wt": 72, "pumpe": False, "phase": "Standby"},
    {"t_wt": 72, "pumpe": False, "phase": None}, {"t_wt": 72, "pumpe": None},
])
def test_pumpe_regel_greift_nur_bei_feuer_stehender_pumpe_und_hitze(werte):
    assert verlauf(Wasserwache(), messung(**werte), bis=600).stufe == OK


def test_pumpe_regel_gilt_auch_im_anheizen_und_in_der_glut():
    for phase in ("Anheizen", "Glut"):
        assert verlauf(Wasserwache(), messung(t_wt=75, pumpe=False, phase=phase), bis=300).stufe == ALARM


def test_pumpe_springt_an_alarm_endet():
    w = Wasserwache()
    verlauf(w, messung(t_wt=75, pumpe=False), bis=150)
    assert w.pruefen(200, messung(t_wt=75, pumpe=True), G).stufe == OK


# ---- Rücklauf ------------------------------------------------------------------------------------


def test_ruecklauf_zu_kalt_erst_nach_fuenf_minuten():
    w = Wasserwache()
    assert verlauf(w, messung(ruecklauf=50), bis=270).stufe == OK
    erg = w.pruefen(300, messung(ruecklauf=50), G)
    assert erg.stufe == WARNUNG and schluessel(erg) == ["ruecklauf_kalt"]


def test_ruecklauf_dip_unter_fuenf_minuten_zaehlt_nicht():
    w = Wasserwache()
    verlauf(w, messung(ruecklauf=50), bis=240)
    w.pruefen(270, messung(ruecklauf=56), G)
    assert verlauf(w, messung(ruecklauf=50), von=300, bis=540).stufe == OK


@pytest.mark.parametrize("phase, pumpe", [("Anheizen", True), ("Glut", True), ("Standby", True), ("Abbrand", False)])
def test_ruecklauf_nur_im_abbrand_oder_ausbrand_und_bei_laufender_pumpe(phase, pumpe):
    assert verlauf(Wasserwache(), messung(ruecklauf=40, phase=phase, pumpe=pumpe), bis=900).stufe == OK


def test_ruecklauf_im_ausbrand_zaehlt_und_endet_mit_der_hysterese():
    w = Wasserwache()
    verlauf(w, messung(ruecklauf=50, phase="Ausbrand"), bis=300)
    assert w.pruefen(330, messung(ruecklauf=56, phase="Ausbrand"), G).stufe == WARNUNG
    assert w.pruefen(360, messung(ruecklauf=57, phase="Ausbrand"), G).stufe == OK
    w2 = Wasserwache()
    verlauf(w2, messung(ruecklauf=50), bis=300)
    assert w2.pruefen(330, messung(ruecklauf=50, phase="Glut"), G).stufe == OK       # Phase vorbei


# ---- Vorlauf --------------------------------------------------------------------------------------


def test_vorlauf_hoch_ist_warnung_und_zu_hoch_ist_alarm():
    w = Wasserwache()
    assert verlauf(w, messung(vorlauf=91), bis=60).stufe == WARNUNG
    assert schluessel(w.pruefen(100, messung(vorlauf=91), G)) == ["vorlauf_hoch"]
    erg = verlauf(w, messung(vorlauf=96), von=120, bis=180)
    assert erg.stufe == ALARM and schluessel(erg) == ["vorlauf_zu_hoch"]          # nur die höhere Stufe


def test_vorlauf_hysterese_je_stufe():
    w = Wasserwache()
    verlauf(w, messung(vorlauf=96), bis=60)
    assert w.pruefen(90, messung(vorlauf=93.0), G).stufe == ALARM                 # 95 − 2 = 93: noch gehalten
    assert w.pruefen(120, messung(vorlauf=92.9), G).stufe == WARNUNG
    assert w.pruefen(150, messung(vorlauf=88.5), G).stufe == WARNUNG               # 90 − 2 = 88: noch gehalten
    assert w.pruefen(180, messung(vorlauf=87.9), G).stufe == OK


def test_alarm_liegt_nie_unter_der_warnung():
    g = Grenzen(vorlauf_warnung=90, vorlauf_alarm=80).stimmig()
    assert g.vorlauf_alarm == 90
    erg = verlauf(Wasserwache(), messung(vorlauf=85), bis=120, grenzen=Grenzen(vorlauf_warnung=90, vorlauf_alarm=80))
    assert erg.stufe == OK


# ---- Puffer -----------------------------------------------------------------------------------------


def test_puffer_voll_ist_ein_hinweis():
    w = Wasserwache()
    erg = verlauf(w, messung(puffer_unten=61), bis=60)
    assert erg.stufe == HINWEIS and "nicht nachlegen" in erg.befunde[0].text
    assert w.pruefen(100, messung(puffer_unten=58.5), G).stufe == HINWEIS
    assert w.pruefen(110, messung(puffer_unten=57.9), G).stufe == OK


# ---- Fühlerfehler -------------------------------------------------------------------------------------


@pytest.mark.parametrize("wert", [1638.3, 120.1, -20.1, float("nan"), float("inf")])
def test_unplausibler_wert_ist_fuehlerfehler_und_die_regeln_ruhen(wert):
    w = Wasserwache()
    erg = verlauf(w, messung(t_wt=wert), bis=300)
    assert erg.stufe == WARNUNG and schluessel(erg) == ["fuehler_t_wt"]
    assert "Wärmetauscher" in erg.befunde[0].text


@pytest.mark.parametrize("wert", [-20.0, 0.0, 120.0])
def test_grenzwerte_des_gueltigen_bereichs_sind_gueltig(wert):
    assert "fuehler_vorlauf" not in schluessel(verlauf(Wasserwache(), messung(vorlauf=wert), bis=300))


def test_offener_fuehler_loest_keinen_alarm_aus_und_beendet_einen_laufenden_nicht_still():
    w = Wasserwache()
    verlauf(w, messung(t_wt=90), bis=60)
    erg = verlauf(w, messung(t_wt=1638.3), von=90, bis=200)
    assert ALARM not in [b.stufe for b in erg.befunde] and "fuehler_t_wt" in schluessel(erg)


def test_kurze_fuehlerstoerung_wird_entprellt():
    w = Wasserwache()
    assert w.pruefen(0, messung(vorlauf=1638.3), G).stufe == OK
    assert w.pruefen(30, messung(vorlauf=70), G).stufe == OK
    assert w.pruefen(60, messung(vorlauf=1638.3), G).stufe == OK


def test_fehlender_wert_ist_erst_nach_fuenf_minuten_ein_hinweis():
    w = Wasserwache()
    assert verlauf(w, messung(ruecklauf=None), bis=270).stufe == OK
    erg = w.pruefen(300, messung(ruecklauf=None), G)
    assert erg.stufe == HINWEIS and schluessel(erg) == ["fehlt_ruecklauf"]
    assert w.pruefen(330, messung(ruecklauf=60), G).stufe == OK


def test_nicht_zugewiesener_fuehler_ist_kein_fehler():
    assert verlauf(Wasserwache(), Messwerte(zugewiesen=frozenset({"t_wt"}), t_wt=60, vorlauf=None), bis=900).stufe == OK


# ---- Stufe und Texte -----------------------------------------------------------------------------------


def test_stufe_ist_die_hoechste_und_die_befunde_stehen_nach_schwere():
    erg = verlauf(Wasserwache(), messung(t_wt=90, puffer_unten=70, vorlauf=92), bis=60)
    assert erg.stufe == ALARM
    assert [b.stufe for b in erg.befunde] == [ALARM, WARNUNG, HINWEIS]
    assert len(erg.texte) == 3


def test_text_bleibt_gleich_wenn_nur_der_messwert_schwankt():
    """Der Text geht als Push raus; ein schwankender Wert darf ihn nicht jedes Mal neu machen."""
    w = Wasserwache()
    verlauf(w, messung(t_wt=86), bis=60)
    a = w.pruefen(90, messung(t_wt=86), G).befunde[0]
    b = w.pruefen(120, messung(t_wt=91), G).befunde[0]
    assert a.text == b.text and a.wert != b.wert


def test_als_dict_ist_serialisierbar():
    import json
    erg = verlauf(Wasserwache(), messung(t_wt=90), bis=60)
    assert json.loads(json.dumps(erg.als_dict()))["stufe"] == ALARM
