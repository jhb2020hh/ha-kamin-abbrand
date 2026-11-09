"""Reinigungserinnerung: reine Rechnung."""
from kern import wartung
from kern.wartung import ERINNERUNG_ABSTAND_S, Stand

H = 3600.0


def test_faelligkeit_und_rest():
    stand = Stand(basis_s=10 * H)
    s = wartung.pruefen(60 * H, stand, 100)
    assert s.aktiv and s.gefahren_h == 50 and s.rest_h == 50 and not s.faellig and not s.bald
    s = wartung.pruefen(110 * H, stand, 100)
    assert s.faellig and s.rest_h == 0 and s.gefahren_h == 100 and s.bald


def test_bald_unter_zehn_prozent_rest():
    stand = Stand(basis_s=0)
    assert not wartung.pruefen(90 * H, stand, 100).bald          # genau 10 % Rest
    s = wartung.pruefen(90.5 * H, stand, 100)
    assert s.bald and not s.faellig


def test_ueberfaellig_rest_wird_nicht_negativ():
    s = wartung.pruefen(250 * H, Stand(basis_s=0), 100)
    assert s.faellig and s.rest_h == 0 and s.gefahren_h == 250


def test_null_ist_aus():
    s = wartung.pruefen(5000 * H, Stand(basis_s=0), 0)
    assert not s.aktiv and not s.faellig and not s.bald
    assert not wartung.erinnern(s, Stand(basis_s=0), 0)


def test_zaehler_darf_nicht_unter_basis_fallen():
    assert wartung.pruefen(5 * H, Stand(basis_s=10 * H), 100).gefahren_h == 0


def test_gereinigt_beginnt_neu():
    stand = Stand(basis_s=0, erinnert=1000.0)
    neu = wartung.gereinigt(120 * H, 5000.0)
    assert neu == Stand(basis_s=120 * H, letzte_reinigung=5000.0, erinnert=None)
    s = wartung.pruefen(130 * H, neu, 100)
    assert not s.faellig and s.gefahren_h == 10 and s.rest_h == 90
    assert wartung.pruefen(130 * H, stand, 100).faellig


def test_zuruecksetzen_behaelt_letzte_reinigung():
    neu = wartung.zuruecksetzen(70 * H, Stand(basis_s=0, letzte_reinigung=42.0, erinnert=1.0))
    assert neu == Stand(basis_s=70 * H, letzte_reinigung=42.0, erinnert=None)


def test_erinnerung_beim_faelligwerden_und_dann_alle_24_h():
    stand = Stand(basis_s=0)
    faellig = wartung.pruefen(100 * H, stand, 100)
    assert wartung.erinnern(faellig, stand, 1000.0)
    stand = wartung.erinnert_am(stand, 1000.0)
    assert not wartung.erinnern(faellig, stand, 1000.0 + ERINNERUNG_ABSTAND_S - 1)
    assert wartung.erinnern(faellig, stand, 1000.0 + ERINNERUNG_ABSTAND_S)


def test_keine_erinnerung_solange_nicht_faellig():
    stand = Stand(basis_s=0)
    assert not wartung.erinnern(wartung.pruefen(99 * H, stand, 100), stand, 0.0)
