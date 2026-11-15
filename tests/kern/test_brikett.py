"""Brennstoff Briketts (Issue #3): nur Regelstand neu, umstellbar in jeder Phase."""
import pytest

from kern.steuerung import Steuerung, brikett_parameter
from kern.typen import (ABBRAND, ABKUEHLEN, AKTION_BRENNSTOFF_HOLZ, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, BRIKETTS,
                        GLUT, HOLZ, NEU, PARITAET, STANDBY, Eingaben, Parameter, Zustand)

T0 = 1_800_000_000.0
STUNDE = 3600.0


def steuerung(phase, seit=0, regelstand=NEU, **felder):
    felder.setdefault("letzter_lauf", T0 - 5)
    z = Zustand(phase=phase, phase_seit=T0 - seit, **felder)
    return Steuerung(Parameter(), z, regelstand=regelstand, takt_s=5)


def eingabe(t, brennstoff=HOLZ, jetzt=T0, **felder):
    felder.setdefault("dp_alter_s", 2.0)
    felder.setdefault("tmax3", t)
    return Eingaben(jetzt=jetzt, t=t, brennstoff=brennstoff, **felder)


def test_parametersatz_ersetzt_nur_die_brikett_werte():
    p = brikett_parameter(Parameter())
    assert (p.startfenster, p.anheizen_timeout, p.klappe_min_abbrand, p.klappe_glut,
            p.glut_max_dauer, p.abkuehlen_max_dauer) == (20, 60, 50, 25, 420, 600)
    assert (p.t_abbrandstart, p.t_ziel, p.t_glut) == (160, 220, 130)


def test_brikett_felder_haben_keinen_live_helfer():
    assert "klappe_glut_brikett" in Parameter.feldnamen()
    assert "klappe_glut_brikett" not in Parameter.helfer_feldnamen()
    assert "klappe_glut" in Parameter.helfer_feldnamen()


# ---------- Anheizen und Fehlstart ----------

def _anheizen(seit, brennstoff):
    st = steuerung(ANHEIZEN, seit, t_phasenstart=140, t_maximum=140)
    return st, st.schritt(eingabe(140.0, brennstoff))


def test_holz_springt_ohne_anstieg_still_zurueck():
    st, _ = _anheizen(11 * 60, HOLZ)
    assert st.zustand.phase == AUSBRAND


def test_briketts_kein_stiller_ruecksprung_und_klappe_ganz_auf():
    st, erg = _anheizen(25 * 60, BRIKETTS)
    assert st.zustand.phase == ANHEIZEN
    assert erg.klappe == 100


def test_briketts_anheizfehler_erst_nach_60_min_mit_eigenem_text():
    st, _ = _anheizen(55 * 60, BRIKETTS)
    assert st.zustand.phase == ANHEIZEN
    st, erg = _anheizen(61 * 60, BRIKETTS)
    assert st.zustand.phase == ANHEIZFEHLER
    assert "Briketts zünden nicht" in erg.meldungen[0].text


def test_anheizfehler_mit_briketts_schliesst_nicht_unter_glut_stellung():
    st = steuerung(ANHEIZFEHLER, 85 * 60, t_phasenstart=140, klappe_soll=30)
    erg = st.schritt(eingabe(60.0, BRIKETTS))
    assert erg.klappe == 25     # Holz liefe hier auf 100 × (1 − 85/90) ≈ 6 %


# ---------- Abbrand ----------

@pytest.mark.parametrize("brennstoff, erwartet", [(HOLZ, 48), (BRIKETTS, 50)])
def test_abbrand_mindestens_50_prozent_mit_briketts(brennstoff, erwartet):
    st = steuerung(ABBRAND, 3600, t_phasenstart=200, t_maximum=300, i_anteil=35, klappe_soll=50,
                   klappe_genau=50.0)
    assert st.schritt(eingabe(300.0, brennstoff)).klappe == erwartet


# ---------- Glut ----------

@pytest.mark.parametrize("brennstoff, erwartet", [(HOLZ, 10), (BRIKETTS, 25)])
def test_glut_klappe(brennstoff, erwartet):
    assert steuerung(GLUT, 600).schritt(eingabe(110.0, brennstoff)).klappe == erwartet


@pytest.mark.parametrize("brennstoff, phase", [(HOLZ, ABKUEHLEN), (BRIKETTS, GLUT)])
def test_glut_haelt_mit_briketts_7_stunden(brennstoff, phase):
    st = steuerung(GLUT, 200 * 60)
    st.schritt(eingabe(110.0, brennstoff))
    assert st.zustand.phase == phase


def test_glut_text_nennt_brikettmenge():
    st = steuerung(ABBRAND, 3600, t_phasenstart=200, t_maximum=250)
    erg = st.schritt(eingabe(120.0, BRIKETTS))
    assert st.zustand.phase == GLUT
    assert "2,12 kg" in erg.meldungen[0].text


# ---------- Nachlauf ----------

def test_brikett_feuer_setzt_nachlauf_10_stunden():
    st = steuerung(ABBRAND, 3600, t_phasenstart=200, t_maximum=230)
    st.schritt(eingabe(220.0, BRIKETTS))
    assert st.zustand.brikett_nachlauf_bis == T0 + 10 * STUNDE


def test_holz_setzt_keinen_nachlauf():
    st = steuerung(ABBRAND, 3600, t_phasenstart=200, t_maximum=230)
    st.schritt(eingabe(220.0, HOLZ))
    assert st.zustand.brikett_nachlauf_bis is None


def test_nachlauf_wird_nie_verkuerzt():
    st = steuerung(ABBRAND, 3600, t_phasenstart=200, t_maximum=230, brikett_nachlauf_bis=T0 + 20 * STUNDE)
    st.schritt(eingabe(220.0, BRIKETTS))
    assert st.zustand.brikett_nachlauf_bis == T0 + 20 * STUNDE


def test_zurueck_auf_holz_in_der_glut_haelt_die_untergrenze():
    """Briketts glühen, dann Holz auf die Restglut: Die Klappe bleibt mindestens auf 25 %."""
    st = steuerung(GLUT, 600, brennstoff=BRIKETTS, brikett_nachlauf_bis=T0 + 3 * STUNDE)
    erg = st.schritt(eingabe(110.0, HOLZ))
    assert erg.klappe == 25
    # Das Zurückstellen zählt selbst als letzter Brikett-Zeitpunkt.
    assert st.zustand.brikett_nachlauf_bis == T0 + 10 * STUNDE


def test_holzabbrand_nach_briketts_regelt_mit_holzwerten():
    st = steuerung(ABBRAND, 3600, t_phasenstart=200, t_maximum=300, i_anteil=35, klappe_soll=50,
                   klappe_genau=50.0, brikett_nachlauf_bis=T0 + 5 * STUNDE)
    assert st.schritt(eingabe(300.0, HOLZ)).klappe == 48


def test_tagesablauf_holz_briketts_holz():
    """Holz am Tag, Briketts am Abend, morgens Holz: Untergrenze 10 h nach dem Brikett-Feuer."""
    st = steuerung(GLUT, 600, brennstoff=HOLZ)
    assert st.schritt(eingabe(110.0, HOLZ, jetzt=T0)).klappe == 10
    # 22 Uhr: Briketts, Abbrand bis 23 Uhr
    st.zustand.phase, st.zustand.phase_seit = ABBRAND, T0
    st.schritt(eingabe(220.0, BRIKETTS, jetzt=T0 + STUNDE))
    ende = T0 + STUNDE + 10 * STUNDE
    assert st.zustand.brikett_nachlauf_bis == ende
    # 6 Uhr: Holz auf die Restglut, Wahl wieder Holz, Glut mit wenig Abgas
    st.zustand.phase, st.zustand.phase_seit = GLUT, T0 + 7 * STUNDE
    st.zustand.letzter_lauf = T0 + 8 * STUNDE - 5
    erg = st.schritt(eingabe(90.0, HOLZ, jetzt=T0 + 8 * STUNDE))
    assert erg.klappe == 25
    assert st.zustand.brikett_nachlauf_bis == T0 + 18 * STUNDE


def test_standby_kalt_aber_nachlauf_offen_haelt_klappe():
    st = steuerung(STANDBY, 3600, brennstoff=BRIKETTS, brikett_nachlauf_bis=T0 + STUNDE)
    erg = st.schritt(eingabe(40.0, BRIKETTS))
    assert erg.klappe == 25
    assert erg.aktionen == []


def test_standby_nachlauf_vorbei_aber_warm_haelt_klappe():
    st = steuerung(STANDBY, 3600, brennstoff=BRIKETTS, brikett_nachlauf_bis=T0 - 1)
    erg = st.schritt(eingabe(60.0, BRIKETTS))
    assert erg.klappe == 25
    assert st.zustand.brikett_nachlauf_bis == T0 - 1


def test_nachlauf_ende_gibt_klappe_frei_und_stellt_auf_holz():
    st = steuerung(STANDBY, 3600, brennstoff=BRIKETTS, brikett_nachlauf_bis=T0 - 1)
    erg = st.schritt(eingabe(40.0, BRIKETTS))
    assert erg.klappe == 0
    assert erg.aktionen == [AKTION_BRENNSTOFF_HOLZ]
    assert st.zustand.brikett_nachlauf_bis is None
    assert "wieder auf Holz" in erg.meldungen[-1].text


def test_briketts_vor_dem_anzuenden_bei_kaltem_ofen_bleiben_gewaehlt():
    st = steuerung(STANDBY, 3600)
    erg = st.schritt(eingabe(20.0, BRIKETTS))
    assert erg.klappe == 0 and erg.aktionen == []
    assert st.zustand.brikett_nachlauf_bis is None


def test_versehentlich_umgestellt_bei_kaltem_ofen_setzt_keinen_nachlauf():
    st = steuerung(STANDBY, 3600, brennstoff=BRIKETTS)
    st.schritt(eingabe(20.0, HOLZ))
    assert st.zustand.brikett_nachlauf_bis is None


def test_handbetrieb_geht_vor():
    st = steuerung(STANDBY, 3600, brennstoff=BRIKETTS, brikett_nachlauf_bis=T0 + STUNDE)
    p = st.parameter
    p.klappe_manuell = 0
    assert st.schritt(eingabe(40.0, BRIKETTS, handbetrieb=True)).klappe == 0


def test_paritaet_kennt_keine_briketts():
    st = steuerung(GLUT, 600, regelstand=PARITAET)
    erg = st.schritt(eingabe(110.0, BRIKETTS))
    assert erg.klappe == 10
    assert st.zustand.brikett_nachlauf_bis is None
