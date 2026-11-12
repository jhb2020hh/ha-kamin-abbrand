"""Regelstand neu: nur die gewollten Abweichungen von der Live-Logik."""
import datetime as dt
import json
from pathlib import Path

import pytest

from kern.signale import signal_bewerten
from kern.steuerung import Steuerung
from kern.typen import (ABBRAND, ABKUEHLEN, AKTION_SNIFFER_RESTART, ANHEIZEN, ANHEIZFEHLER, AUSBRAND,
                        GLUT, NEU, SIGNAL_FRISCH, SIGNAL_STILL, SIGNAL_STUMM, STANDBY, STOERUNG,
                        TUER_OFFEN, Eingaben, Parameter, Zustand)

T0 = 1_800_000_000.0
FIXTUR = Path(__file__).parents[1] / "fixtures" / "rueckspiel_2026-11-05.json"


def steuerung(phase=ABBRAND, seit=3600, **felder):
    z = Zustand(phase=phase, phase_seit=T0 - seit, letzter_lauf=T0 - 5, **felder)
    return Steuerung(Parameter(), z, regelstand=NEU, takt_s=5)


def eingabe(t=200.0, jetzt=T0, **felder):
    felder.setdefault("dp_alter_s", 2.0)
    return Eingaben(jetzt=jetzt, t=t, **felder)


# ---------- Signal: Stillstand gegen Funkstille ----------

@pytest.mark.parametrize("can, erwartet", [(0, SIGNAL_FRISCH), (30, SIGNAL_FRISCH),
                                           (31, SIGNAL_STILL), (60, SIGNAL_STILL),
                                           (61, SIGNAL_STUMM)])
def test_can_alter_entscheidet_allein(can, erwartet):
    e = eingabe(t_alter_s=5000, dp_alter_s=5000, can_alter_s=can)
    assert signal_bewerten(e, Parameter(), STANDBY) == erwartet


def test_abgas_steht_aber_druck_lebt_ist_frisch():
    """Der Fall 03.11.: T stand 444 s still, ohne Ausfall."""
    assert signal_bewerten(eingabe(t_alter_s=444, dp_alter_s=4), Parameter(), AUSBRAND) == SIGNAL_FRISCH


def test_bei_feuer_gilt_die_kurze_grenze():
    e = eingabe(t_alter_s=121, dp_alter_s=121)
    assert signal_bewerten(e, Parameter(), ABBRAND) == SIGNAL_STUMM


def test_in_ruhe_gilt_der_timeout():
    """Der Druck stand in Ruhe schon 14 min still, ohne Ausfall."""
    e = eingabe(t_alter_s=590, dp_alter_s=590)
    assert signal_bewerten(e, Parameter(), STANDBY) == SIGNAL_STILL
    e = eingabe(t_alter_s=601, dp_alter_s=601)
    assert signal_bewerten(e, Parameter(), STANDBY) == SIGNAL_STUMM


def test_ohne_druckwert_keine_kurze_grenze():
    e = eingabe(t_alter_s=444, dp_alter_s=None)
    assert signal_bewerten(e, Parameter(), ABBRAND) == SIGNAL_STILL


def test_stumm_im_abbrand_oeffnet_und_startet_sniffer_neu():
    s = steuerung(ABBRAND, t_maximum=220, t_phasenstart=100, i_anteil=40, klappe_soll=40)
    e = s.schritt(eingabe(t_alter_s=130, dp_alter_s=130))
    assert s.zustand.phase == STOERUNG and e.klappe == 100
    assert AKTION_SNIFFER_RESTART in e.aktionen and e.signal == SIGNAL_STUMM


def test_stumm_kalt_ohne_feuer_oeffnet_nicht():
    """Sonst stünde die Klappe nachts auf, sobald der LUC schweigt."""
    s = steuerung(STANDBY)
    s.schritt(eingabe(t=25, can_alter_s=500))
    assert s.zustand.phase == STANDBY and s.zustand.klappe_soll == 0


def test_stoerung_endet_erst_bei_frischem_signal():
    s = steuerung(STOERUNG, klappe_soll=100)
    s.schritt(eingabe(t=200, t_alter_s=30, dp_alter_s=70, can_alter_s=45))
    assert s.zustand.phase == STOERUNG
    s.schritt(eingabe(t=200, jetzt=T0 + 5, can_alter_s=2))
    assert s.zustand.phase == ANHEIZEN


def test_eingefrorener_wert_ueber_uebertemperatur_oeffnet_trotzdem():
    s = steuerung(STOERUNG, klappe_soll=100)
    assert s.schritt(eingabe(t=450, t_alter_s=900, dp_alter_s=900)).klappe == 100


def test_fehlstart_regel_greift_nicht_bei_stummem_signal():
    s = steuerung(ANHEIZEN, seit=700, t_maximum=52, t_phasenstart=52, klappe_soll=100)
    s.schritt(eingabe(t=52, t_alter_s=9000, dp_alter_s=9000))
    assert s.zustand.phase == STOERUNG


# ---------- Rückspiel 05.11. ----------

def _ts(text):
    return dt.datetime.fromisoformat(f"2026-11-05T{text}+01:00").timestamp()


def test_rueckspiel_05_11_stoerung_statt_abkuehlen():
    """Der Sniffer schweigt seit 05:24:18. Das echte Feuer darf nicht erstickt werden."""
    daten = json.loads(FIXTUR.read_text(encoding="utf8"))
    letzter_wert = _ts("05:24:18.37")
    letzter_druck = _ts("05:09:31")
    tuer = [(_ts(a), offen) for a, offen in daten["tuer"]]
    twt = [(_ts(a), w) for a, w in daten["t_wt"]]

    def wert_zu(reihe, zeit, ersatz):
        wert = ersatz
        for ab, w in reihe:
            if ab <= zeit:
                wert = w
        return wert

    start = _ts("07:30:00")
    z = Zustand(phase=STANDBY, phase_seit=start - 7200, t_maximum=52, t_phasenstart=52, letzter_lauf=start)
    s = Steuerung(Parameter(), z, regelstand=NEU, takt_s=5)
    stoerung_ab = None
    for jetzt in range(int(start), int(_ts("09:00:00")), 5):
        twt_jetzt = wert_zu(twt, jetzt, 50.3)
        twt_alt = wert_zu(twt, jetzt - 600, 50.3)
        e = s.schritt(Eingaben(jetzt=float(jetzt), t=52.0, t_alter_s=jetzt - letzter_wert,
                               dp_alter_s=jetzt - letzter_druck, tuer_offen=wert_zu(tuer, jetzt, False),
                               twt_roh=twt_jetzt, twt_anstieg=twt_jetzt - twt_alt))
        if s.zustand.phase == STOERUNG and stoerung_ab is None:
            stoerung_ab = jetzt
        if jetzt >= _ts("08:12:00") and not s.zustand.phase == TUER_OFFEN:
            assert s.zustand.phase == STOERUNG and e.klappe == 100
    assert _ts("08:09:58") <= stoerung_ab <= _ts("08:11:30")


# ---------- Phantom-Anheizen ----------

# Aus dem Recorder: (Sekunden ab Beginn, T, Gradient), Tür blieb zu.
PHANTOM_01_11 = [(0, 173.0, -3.6), (18, 175.0, -2.0), (100, 177.0, 1.0), (110, 179.0, 2.1),
                 (130, 181.0, 3.2), (150, 184.0, 5.5), (180, 185.0, 5.0), (210, 187.0, 5.8),
                 (240, 188.0, 6.3), (260, 188.0, 6.3)]
PHANTOM_02_11 = [(0, 191.0, 0.0), (130, 193.0, 1.0), (150, 195.0, 2.0), (190, 197.0, 2.5),
                 (220, 199.0, 3.1), (240, 200.0, 3.4), (280, 202.0, 3.3), (380, 204.0, 3.0)]


@pytest.mark.parametrize("verlauf", [PHANTOM_01_11, PHANTOM_02_11], ids=["01.11.", "02.11."])
def test_aufflackern_ohne_tuer_bleibt_ausbrand(verlauf):
    s = steuerung(AUSBRAND, seit=600, t_maximum=220, t_phasenstart=220, klappe_soll=60, t_tief=verlauf[0][1])
    for sekunde, t, g in verlauf:
        s.schritt(eingabe(t=t, g=g, jetzt=T0 + sekunde))
        assert s.zustand.phase == AUSBRAND


def test_ohne_tuer_aber_deutlicher_anstieg_ist_nachlegen():
    """Defekter Türkontakt darf echtes Nachlegen nicht in der Glut-Klappe ersticken."""
    s = steuerung(GLUT, seit=600, klappe_soll=10, t_tief=140.0)
    s.schritt(eingabe(t=160, g=5))
    assert s.zustand.phase == ANHEIZEN


def test_tuer_kurz_zuvor_reicht_der_gradient():
    s = steuerung(AUSBRAND, seit=600, klappe_soll=60, t_tief=200.0, letzte_tuer=T0 - 200)
    s.schritt(eingabe(t=205, g=4))
    assert s.zustand.phase == ANHEIZEN


def test_ohne_tuerkontakt_gilt_der_gradient_wie_live():
    s = steuerung(AUSBRAND, seit=600, klappe_soll=60, t_tief=200.0)
    s.schritt(eingabe(t=205, g=4, tuerkontakt_vorhanden=False))
    assert s.zustand.phase == ANHEIZEN


# ---------- Klappe ----------

@pytest.mark.parametrize("phase, seit", [(ABKUEHLEN, 1200), (ANHEIZFEHLER, 5300)])
def test_nie_ganz_zu_solange_glut(phase, seit):
    s = steuerung(phase, seit=seit, klappe_soll=10)
    assert s.schritt(eingabe(t=100)).klappe == Parameter().klappe_glut


def test_steigendes_twt_haelt_klappe_auch_unter_t_abkuehlen():
    s = steuerung(ABKUEHLEN, seit=1200, klappe_soll=5)
    assert s.schritt(eingabe(t=70, twt_anstieg=3.5)).klappe == Parameter().klappe_glut


def test_kalter_ofen_darf_ganz_schliessen():
    s = steuerung(STANDBY)
    assert s.schritt(eingabe(t=30)).klappe == 0


def test_schliessrate_nach_zeit_unabhaengig_von_der_laufzahl():
    """10 % je 30 s, auch wenn zusätzliche Läufe dazwischenkommen."""
    s = steuerung(ABBRAND, t_maximum=300, t_phasenstart=100, i_anteil=35, klappe_soll=80,
                  klappe_genau=80.0)
    s.zustand.letzter_lauf = T0
    for i in range(1, 31):
        ergebnis = s.schritt(eingabe(t=300, jetzt=T0 + i, ausloeser="bedienung"))
    assert ergebnis.klappe == 70


def test_i_anteil_ohne_totzone():
    """Live bleibt der I-Anteil bei |e| unter 1,4 K stehen."""
    s = steuerung(ABBRAND, t_maximum=230, t_phasenstart=100, i_anteil=50.0, klappe_soll=50)
    for i in range(12):
        s.schritt(eingabe(t=219.5, jetzt=T0 + 5 * i))
    assert s.zustand.i_anteil > 50.0


def test_tuer_wirkt_auch_ohne_messwert():
    s = steuerung(ABBRAND, t_maximum=230, t_phasenstart=180, i_anteil=50, klappe_soll=50)
    e = s.schritt(eingabe(t=None, tuer_offen=True))
    assert s.zustand.phase == TUER_OFFEN and e.klappe == 100
    assert s.zustand.t_phasenstart == 180 and s.zustand.t_maximum == 230


# ---------- Fehler quittieren ----------

def test_anheizfehler_quittiert_kalter_ofen_geht_in_standby():
    from kern.typen import QUITTIEREN
    s = steuerung(phase=ANHEIZFEHLER, seit=600)
    r = s.schritt(eingabe(t=30.0, ausloeser=QUITTIEREN))
    assert r.uebergang == (ANHEIZFEHLER, STANDBY)
    assert any("quittiert" in m.text for m in r.meldungen)


def test_anheizfehler_quittiert_heisser_ofen_geht_in_passende_phase():
    from kern.typen import QUITTIEREN
    s = steuerung(phase=ANHEIZFEHLER, seit=600)
    r = s.schritt(eingabe(t=Parameter().t_glut + 20, ausloeser=QUITTIEREN))
    assert r.zustand.phase == AUSBRAND


def test_stoerung_quittiert_bei_frischem_messwert():
    from kern.typen import QUITTIEREN
    s = steuerung(phase=STOERUNG, seit=600)
    r = s.schritt(eingabe(t=30.0, ausloeser=QUITTIEREN))
    assert r.zustand.phase == STANDBY


def test_stoerung_bleibt_wenn_fuehler_stumm():
    from kern.typen import QUITTIEREN
    s = steuerung(phase=STOERUNG, seit=600)
    r = s.schritt(eingabe(t=30.0, t_alter_s=5000, dp_alter_s=5000, can_alter_s=500, ausloeser=QUITTIEREN))
    assert r.zustand.phase == STOERUNG and r.uebergang is None
    assert any("nicht möglich" in m.text for m in r.meldungen)
    assert r.klappe == 100


def test_quittieren_ohne_fehler_aendert_nichts():
    from kern.typen import QUITTIEREN
    s = steuerung(phase=ABBRAND, seit=3600)
    r = s.schritt(eingabe(t=200.0, ausloeser=QUITTIEREN))
    assert r.regel != "Fehler quittiert → passende Phase"
