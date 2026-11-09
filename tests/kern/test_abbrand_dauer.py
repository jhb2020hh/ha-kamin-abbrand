"""Laufzeiten nach den Regeln der Automation „Dauer erfassen“."""
from kern.abbrand_dauer import DauerStand, anzeige, aus_daten, fortschreiben
from kern.typen import ABBRAND, ANHEIZEN, AUSBRAND, GLUT, STANDBY, TUER_OFFEN

TAG = "2026-11-09"


def folge(s, *schritte, tag=TAG):
    for phase, minute in schritte:
        fortschreiben(s, phase, minute * 60.0, minute * 60.0, tag)


def test_abbrand_laeuft_von_tuer_bis_glut():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13), (AUSBRAND, 40), (GLUT, 55))
    assert s.letzte_min == 45.0 and s.anzahl_gesamt == 1 and s.anzahl_heute == 1 and not s.laeuft


def test_fehlstart_zaehlt_nicht():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (STANDBY, 20))
    assert s.anzahl_gesamt == 0 and s.letzte_min is None and not s.laeuft


def test_nachlegen_beendet_den_vorigen_beim_oeffnen_der_tuer():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13), (AUSBRAND, 40), (TUER_OFFEN, 50), (ANHEIZEN, 51), (ABBRAND, 53))
    assert s.letzte_min == 40.0 and s.anzahl_gesamt == 1 and s.laeuft and s.start == 50 * 60.0


def test_mitternacht_setzt_nur_heute_zurueck():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13), (GLUT, 40))
    fortschreiben(s, GLUT, 40 * 60.0, 90 * 60.0, "2026-11-10")
    assert s.anzahl_heute == 0 and s.summe_heute_min == 0 and s.anzahl_gesamt == 1


def test_abbrand_ueber_mitternacht_zaehlt_zum_tag_des_endes():
    s = DauerStand()
    folge(s, (STANDBY, 0), (TUER_OFFEN, 10), (ANHEIZEN, 11), (ABBRAND, 13))
    folge(s, (GLUT, 70), tag="2026-11-10")
    assert s.anzahl_heute == 1 and s.summe_heute_min == 60.0


def test_erster_lauf_bucht_nichts():
    s = DauerStand(laeuft=True, start=60.0)
    assert fortschreiben(s, GLUT, 600.0, 600.0, TAG)
    assert s.anzahl_gesamt == 0 and s.laeuft


def test_anzeige_ohne_abbrand_hat_keinen_schnitt():
    a = anzeige(DauerStand(), 1000.0)
    assert a["aktuell_min"] is None and a["schnitt_heute_min"] is None and a["schnitt_gesamt_min"] is None


def test_anzeige_rechnet_schnitt_und_laufzeit():
    a = anzeige(DauerStand(laeuft=True, start=0.0, summe_gesamt_min=4932.0, anzahl_gesamt=102, summe_heute_min=90.0, anzahl_heute=2), 600.0)
    assert a["aktuell_min"] == 10.0 and a["schnitt_gesamt_min"] == 48.4 and a["schnitt_heute_min"] == 45.0


def test_verdorbener_stand_faengt_neu_an():
    assert aus_daten("kaputt") == DauerStand()
    assert aus_daten({"anzahl_gesamt": 3, "unbekannt": 1}).anzahl_gesamt == 3
