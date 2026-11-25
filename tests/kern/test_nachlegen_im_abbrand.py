"""Nachlegen im Abbrand (Issue #4): Tür auf/zu im heißen Abbrand führt ohne Anheizen zurück."""
from kern.abbrand_dauer import DauerStand, anzeige, fortschreiben
from kern.steuerung import Steuerung
from kern.typen import (ABBRAND, ANHEIZEN, ANHEIZFEHLER, AUSBRAND, GLUT, NEU, PARITAET, STANDBY, TUER_OFFEN,
                        Eingaben, Parameter, Zustand)

T0 = 1_800_000_000.0
TAKT = 5


def steuerung(seit=40 * 60, regelstand=NEU, **felder):
    felder.setdefault("letzter_lauf", T0 - TAKT)
    felder.setdefault("t_phasenstart", 200)
    felder.setdefault("t_maximum", 300)
    felder.setdefault("i_anteil", 35)
    felder.setdefault("klappe_soll", 50)
    felder.setdefault("klappe_genau", 50.0)
    z = Zustand(phase=ABBRAND, phase_seit=T0 - seit, **felder)
    return Steuerung(Parameter(), z, regelstand=regelstand, takt_s=TAKT)


def eingabe(t, jetzt, tuer_offen=False, **felder):
    felder.setdefault("dp_alter_s", 2.0)
    felder.setdefault("tmax3", t)
    felder.setdefault("twt_roh", 50.0)              # Wasser weit unter dem Maximum
    return Eingaben(jetzt=jetzt, t=t, tuer_offen=tuer_offen, **felder)


def tuer(st, t, offen_s, start=T0, t_zu=None):
    """Tür ab start für offen_s Sekunden auf, danach ein Schritt mit geschlossener Tür."""
    jetzt = start
    while jetzt < start + offen_s:
        st.schritt(eingabe(t, jetzt, tuer_offen=True))
        jetzt += TAKT
    return st.schritt(eingabe(t if t_zu is None else t_zu, jetzt)), jetzt


# ---------- Rückkehr in den Abbrand ----------

def test_heisser_abbrand_kehrt_ohne_anheizen_zurueck():
    st = steuerung()
    seit = st.zustand.phase_seit
    _, zu = tuer(st, 250.0, 60)
    z = st.zustand
    assert z.phase == ABBRAND
    assert z.phase_seit == seit                     # Abbranddauer läuft weiter
    assert z.nachgelegt_seit == float(int(zu))
    assert z.nachlegen_anzahl == 1
    assert not z.tuer_aus_abbrand and z.abbrand_seit is None


def test_i_anteil_bleibt_beim_zurueckkehren():
    st = steuerung(i_anteil=27.5)
    st.schritt(eingabe(250.0, T0, tuer_offen=True))
    i_offen = st.zustand.i_anteil
    tuer(st, 250.0, 30, start=T0 + TAKT)
    assert st.zustand.phase == ABBRAND
    assert st.zustand.i_anteil == i_offen


def test_zweites_nachlegen_zaehlt_weiter():
    st = steuerung()
    _, zu = tuer(st, 250.0, 60)
    _, zu2 = tuer(st, 250.0, 60, start=zu + 20 * 60)
    assert st.zustand.nachlegen_anzahl == 2
    assert st.zustand.nachgelegt_seit == float(int(zu2))


def test_kalter_abbrand_geht_wie_bisher_ins_anheizen():
    st = steuerung()
    tuer(st, 250.0, 60, t_zu=150.0)                 # unter t_abbrandstart 160 °C
    assert st.zustand.phase == ANHEIZEN
    assert st.zustand.nachlegen_anzahl == 0


def test_ohne_messwert_geht_es_ins_anheizen():
    st = steuerung()
    jetzt = T0
    for _ in range(6):
        st.schritt(eingabe(250.0, jetzt, tuer_offen=True))
        jetzt += TAKT
    st.schritt(Eingaben(jetzt=jetzt, t=None, tuer_offen=False, dp_alter_s=2.0))
    assert st.zustand.phase != ABBRAND


def test_tuer_aus_ausbrand_bleibt_beim_anheizen():
    st = steuerung()
    st.zustand.phase = AUSBRAND
    tuer(st, 250.0, 60)
    assert st.zustand.phase == ANHEIZEN


def test_paritaet_bleibt_beim_anheizen():
    st = steuerung(regelstand=PARITAET)
    tuer(st, 250.0, 60)
    assert st.zustand.phase == ANHEIZEN


def test_kurzes_oeffnen_ist_kein_nachlegen():
    st = steuerung(t_maximum=300)
    _, _ = tuer(st, 250.0, 5)                       # unter tuer_nachlegen_min_s = 10 s
    z = st.zustand
    assert z.phase == ABBRAND
    assert z.nachgelegt_seit is None and z.nachlegen_anzahl == 0
    assert z.t_maximum == 300                       # Maximum bleibt


# ---------- Fristen ab dem Nachlegen ----------

def test_ausbrand_frist_zaehlt_ab_nachlegen():
    """40 min im Abbrand, nachgelegt vor 5 min: trotz deutlichem Abfall noch kein Ausbrand."""
    st = steuerung(t_maximum=300, nachgelegt_seit=T0 - 5 * 60, nachlegen_anzahl=1)
    st.schritt(eingabe(190.0, T0, tmax3=190.0))
    assert st.zustand.phase == ABBRAND


def test_ausbrand_ohne_nachlegen_wie_bisher():
    st = steuerung(t_maximum=300)
    st.schritt(eingabe(190.0, T0, tmax3=190.0))
    assert st.zustand.phase == AUSBRAND


def test_zusammenbruch_kurz_nach_nachlegen_ist_anheizfehler():
    st = steuerung(nachgelegt_seit=T0 - 5 * 60, nachlegen_anzahl=1)
    st.schritt(eingabe(120.0, T0))                  # unter t_glut 130 °C
    assert st.zustand.phase == ANHEIZFEHLER


def test_ohne_nachlegen_nach_40_min_kein_anheizfehler():
    st = steuerung()
    st.schritt(eingabe(120.0, T0))
    assert st.zustand.phase != ANHEIZFEHLER


def test_entgasungs_minimum_gilt_ab_nachlegen():
    p = Parameter()
    st = steuerung(nachgelegt_seit=T0 - 60, nachlegen_anzahl=1, klappe_soll=p.klappe_min_abbrand,
                   klappe_genau=float(p.klappe_min_abbrand), i_anteil=0)
    erg = st.schritt(eingabe(320.0, T0))            # weit über Ziel: Regler will zu
    assert erg.klappe >= p.klappe_min_entgasung


def test_entgasungs_minimum_endet_nach_der_frist():
    p = Parameter()
    st = steuerung(nachgelegt_seit=T0 - 20 * 60, nachlegen_anzahl=1, klappe_soll=p.klappe_min_abbrand,
                   klappe_genau=float(p.klappe_min_abbrand), i_anteil=0)
    erg = st.schritt(eingabe(320.0, T0))
    assert erg.klappe < p.klappe_min_entgasung


# ---------- Dauererfassung ----------

TAG = "2026-11-26"


def folge(s, *schritte):
    for phase, minute, *n in schritte:
        fortschreiben(s, phase, minute * 60.0, minute * 60.0, TAG, nachlegen=n[0] if n else 0)


def test_dauer_ein_abbrand_ueber_das_nachlegen():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13),
          (TUER_OFFEN, 50), (ABBRAND, 51, 1), (TUER_OFFEN, 90), (ABBRAND, 91, 2), (GLUT, 130))
    assert s.anzahl_gesamt == 1 and s.letzte_min == 120.0
    assert s.letzte_nachlegen == 2
    assert anzeige(s, 130 * 60.0)["nachlegen_letzte"] == 2


def test_dauer_nachlegezahl_waehrend_des_abbrands():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13), (TUER_OFFEN, 50), (ABBRAND, 51, 1))
    assert s.laeuft and anzeige(s, 60 * 60.0)["nachlegen_aktuell"] == 1


def test_dauer_mit_anheizen_wie_bisher():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13),
          (TUER_OFFEN, 50), (ANHEIZEN, 51), (ABBRAND, 53))
    assert s.letzte_min == 40.0 and s.anzahl_gesamt == 1 and s.laeuft and s.start == 50 * 60.0
    assert s.nachlegen == 0
