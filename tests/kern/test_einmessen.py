"""Einmessen: Zyklen zählen, Punkte vorschlagen, Parameter ableiten."""
import json
import math
from pathlib import Path

import pytest

from kern import einmessen as em
from kern.einmessen import (FEUER, FERTIG, KALT, KEIN_FEUER, NACHLEGEN, Ereignis, Probe, Punkt,
                            Zyklenzaehler, ereignisse_ermitteln, punkte_vorschlagen, werte_ableiten)
from kern.typen import Parameter

T0 = 1_800_000_000.0
TAKT = 5
FIXTUR = Path(__file__).parents[1] / "fixtures" / "ableitung_2026-11-08.json"

# Grenzen wie in anbindung/einstellungen.py (minimum, maximum, schritt)
GRENZEN = {
    "t_kaltstart_ende": (150, 300, 5), "t_abbrandstart": (80, 400, 5), "gradient_start": (0.5, 30, 0.5),
    "t_ziel": (120, 500, 5), "t_ziel_eco": (120, 500, 5), "t_ziel_normal": (120, 500, 5),
    "t_ziel_power": (120, 500, 5), "ausbrand_abfall": (5, 150, 5), "t_glut": (60, 300, 5),
    "t_abkuehlen": (40, 200, 5), "t_uebertemperatur": (200, 900, 10),
}

# Modell eines Abbrands, Zeiten in Minuten
VORLAUF = 5         # kalter Ofen vor dem Anzünden, Tür die letzten 2 min offen
ANSTIEG = 15        # Anstieg als Logistik, steilste Stelle nach 4 bzw. 6 min
PLATEAU = 25
SPITZE = 250.0
NACHLEGEN_BEI = 150.0
FALL_TAU = 40.0     # Abfall exponentiell auf 20 °C zu
TUER_DAUER_S = 60


def _logistik(tau, mitte, breite):
    return 1 / (1 + math.exp(-(tau - mitte) / breite))


def _anstieg(tau, von, mitte, breite):
    """Von `von` auf SPITZE, stetig ab tau = 0."""
    l0 = _logistik(0, mitte, breite)
    return von + (SPITZE - von) * (_logistik(tau, mitte, breite) - l0) / (1 - l0)


def abbrand(zyklen=1, tuer=True, prellen=False, nachlauf_min=120, wellen=1.5):
    """Kaltstart 20 °C → Anstieg → Plateau → Abfall → Nachlegen bei 150 °C → …, Takt 5 s.

    Liefert (proben, nachlegezeiten). Der letzte Zyklus fällt `nachlauf_min` lang ab.
    """
    proben, nachlegen = [], []
    zeit = T0

    def probe(t, offen=False):
        nonlocal zeit
        welle = wellen * math.sin((zeit - T0) / 600 * 2 * math.pi)
        proben.append(Probe(zeit, t + welle, None, (offen if tuer else None)))
        zeit += TAKT

    for s in range(0, VORLAUF * 60, TAKT):
        probe(20.0, offen=s >= (VORLAUF - 2) * 60)
    von = 20.0
    for k in range(zyklen):
        mitte, breite = (6, 1.5) if k == 0 else (4, 1.0)
        for s in range(0, ANSTIEG * 60, TAKT):
            offen = k > 0 and s < TUER_DAUER_S
            if prellen and k > 0 and s in (10, 20):
                offen = False
            probe(_anstieg(s / 60, von, mitte, breite), offen)
        t_ende = proben[-1].t - wellen * math.sin((zeit - TAKT - T0) / 600 * 2 * math.pi)
        for _ in range(0, PLATEAU * 60, TAKT):
            probe(t_ende)
        letzter = k == zyklen - 1
        s = 0
        while True:
            t = 20 + (t_ende - 20) * math.exp(-s / 60 / FALL_TAU)
            if not letzter and t <= NACHLEGEN_BEI:
                break
            if letzter and s >= nachlauf_min * 60:
                break
            probe(t)
            s += TAKT
        if not letzter:
            nachlegen.append(zeit)
            von = t
    return proben, nachlegen


def arten(ereignisse):
    return [e.art for e in ereignisse]


def nur(ereignisse, art):
    return [e for e in ereignisse if e.art == art]


# ---- Zyklenzähler ----

def test_ein_zyklus_mit_tuer_fertig_nach_dem_nachlegen():
    proben, nachlegen = abbrand(zyklen=2)
    ev = ereignisse_ermitteln(proben, n_soll=1, tuerkontakt=True)
    assert arten(ev) == [FEUER, NACHLEGEN, FERTIG, KALT]
    feuer, nach, fertig, kalt = ev
    assert T0 + VORLAUF * 60 < feuer.zeit < T0 + (VORLAUF + 8) * 60 and feuer.zyklus == 1
    assert nach.zeit == nachlegen[0] and nach.zyklus == 2
    assert fertig.zeit == nachlegen[0] + TUER_DAUER_S + em.TUER_ZU_S and fertig.zyklus == 2
    assert kalt.zeit > fertig.zeit


def test_drei_zyklen_mit_tuer():
    proben, nachlegen = abbrand(zyklen=4)
    z = Zyklenzaehler(3, True)
    gemeldet = [e for e in (z.schritt(p) for p in proben) if e]
    assert gemeldet == z.ereignisse
    assert arten(gemeldet) == [FEUER, NACHLEGEN, NACHLEGEN, NACHLEGEN, FERTIG, KALT]
    assert [e.zeit for e in nur(gemeldet, NACHLEGEN)] == nachlegen
    assert [e.zyklus for e in nur(gemeldet, NACHLEGEN)] == [2, 3, 4]
    assert z.fertig and z.beendet and z.zyklus == 4 and z.nachgelegt == 3


def test_nach_fertig_zaehlt_er_weiter():
    proben, nachlegen = abbrand(zyklen=3)
    ev = ereignisse_ermitteln(proben, n_soll=1, tuerkontakt=True)
    assert arten(ev) == [FEUER, NACHLEGEN, FERTIG, NACHLEGEN, KALT]
    assert nur(ev, NACHLEGEN)[-1].zyklus == 3


def test_zwei_zyklen_ohne_tuer_ueber_die_gradientenumkehr():
    proben, nachlegen = abbrand(zyklen=3, tuer=False)
    ev = ereignisse_ermitteln(proben, n_soll=2, tuerkontakt=False)
    assert arten(ev) == [FEUER, NACHLEGEN, NACHLEGEN, FERTIG, KALT]
    for e, soll in zip(nur(ev, NACHLEGEN), nachlegen):
        assert abs(e.zeit - soll) <= 60          # Tiefstwert = Zeitpunkt des Auflegens
    zweites = nur(ev, NACHLEGEN)[1]
    fertig = nur(ev, FERTIG)[0]
    # Fertig mit der Probe nach dem Erkennen, das Erkennen braucht den Anstieg von 20 K
    assert zweites.zeit < fertig.zeit < zweites.zeit + 8 * 60


def test_ohne_tuer_kein_nachlegen_durch_wellen_im_plateau():
    proben, _ = abbrand(zyklen=1, tuer=False, wellen=4.0)
    ev = ereignisse_ermitteln(proben, n_soll=1, tuerkontakt=False)
    assert arten(ev) == [FEUER, KALT]


def test_tuerprellen_zaehlt_einmal():
    proben, nachlegen = abbrand(zyklen=2, prellen=True)
    z = Zyklenzaehler(1, True)
    ev = [e for e in (z.schritt(p) for p in proben) if e]
    assert z.nachgelegt == 1 and len(nur(ev, NACHLEGEN)) == 1
    # Fertig erst, wenn die Tür 30 s zu ist: das Prellen verschiebt es bis nach dem letzten Zu
    assert nur(ev, FERTIG)[0].zeit == nachlegen[0] + TUER_DAUER_S + em.TUER_ZU_S


def test_kurzes_oeffnen_kurz_nach_dem_feuer_ist_kein_nachlegen():
    proben, _ = abbrand(zyklen=1)
    feuer = ereignisse_ermitteln(proben, 1, True)[0].zeit
    geaendert = [Probe(p.zeit, p.t, None, True) if feuer + 120 <= p.zeit < feuer + 150 else p for p in proben]
    ev = ereignisse_ermitteln(geaendert, 1, True)
    assert NACHLEGEN not in arten(ev)


def test_anzuend_oeffnung_vor_dem_feuer_zaehlt_nicht():
    proben, _ = abbrand(zyklen=1)
    # Tür bleibt bis lange nach dem Feuer offen
    geaendert = [Probe(p.zeit, p.t, None, True) if p.zeit < T0 + (VORLAUF + 12) * 60 else p for p in proben]
    ev = ereignisse_ermitteln(geaendert, 0, True)
    assert arten(ev) == [FEUER, FERTIG, KALT]
    assert ev[1].zeit == T0 + (VORLAUF + 12) * 60 + em.TUER_ZU_S


def test_kein_feuer():
    proben = [Probe(T0 + s, 20.0 + 0.001 * s, tuer_offen=False) for s in range(0, 7200, TAKT)]
    z = Zyklenzaehler(2, True, kein_feuer_s=3600)
    ev = [e for e in (z.schritt(p) for p in proben) if e]
    assert arten(ev) == [KEIN_FEUER] and ev[0].zeit == T0 + 3600 and ev[0].zyklus == 0
    assert z.beendet and not z.fertig and z.zyklus == 0


def test_kalt_erst_nach_fertig_und_nur_einmal():
    proben, _ = abbrand(zyklen=2, nachlauf_min=180)
    ev = ereignisse_ermitteln(proben, 1, True, t_kalt=50.0)
    kalt = nur(ev, KALT)
    assert len(kalt) == 1 and ev[-1].art == KALT
    assert ev.index(kalt[0]) > ev.index(nur(ev, FERTIG)[0])
    t_dort = next(p.t for p in proben if p.zeit == kalt[0].zeit)
    assert t_dort < 50.0
    # beim Kaltstart von 20 °C meldet er nicht schon beim Feuer „kalt“
    assert all(p.t >= 50 for p in proben if nur(ev, FEUER)[0].zeit + 600 < p.zeit < kalt[0].zeit)


def test_feuer_geht_vor_dem_nachlegen_aus():
    proben, _ = abbrand(zyklen=1)
    ev = ereignisse_ermitteln(proben, 2, True)
    assert arten(ev) == [FEUER, KALT]


def test_gradient_aus_der_probe_hat_vorrang():
    z = Zyklenzaehler(1, False)
    for s in range(0, 150, TAKT):
        e = z.schritt(Probe(T0 + s, 20.0, g=5.0))
    assert e is None and z.zyklus == 0              # 5 K/min gemeldet, aber kein Anstieg in K
    for s in range(150, 300, TAKT):
        e = z.schritt(Probe(T0 + s, 20.0 + (s - 150) * 0.05, g=5.0)) or e
    assert e is not None and e.art == FEUER


def test_messluecken_stoeren_nicht():
    proben, _ = abbrand(zyklen=2)
    mit_luecken = [Probe(p.zeit, None if i % 7 == 0 else p.t, None, p.tuer_offen) for i, p in enumerate(proben)]
    assert arten(ereignisse_ermitteln(mit_luecken, 1, True)) == [FEUER, NACHLEGEN, FERTIG, KALT]


# ---- Punkte ----

def _zeitfenster(punkte, art, zyklus):
    return [p.zeit for p in punkte if p.art == art and p.zyklus == zyklus]


def test_punkte_liegen_in_plausiblen_zeitfenstern():
    proben, nachlegen = abbrand(zyklen=3)
    ev = ereignisse_ermitteln(proben, 2, True)
    punkte = punkte_vorschlagen(proben, ev)
    assert all(p.auto for p in punkte) and all(p.art in em.PUNKTARTEN for p in punkte)
    assert not [p for p in punkte if p.art == "zu_heiss"]
    assert punkte == sorted(punkte, key=lambda p: p.zeit)
    zuendung = T0 + VORLAUF * 60
    # Zyklus 1: steilste Stelle nach 6 min, halbe Steigung etwa 2 min später
    (anheizen,) = _zeitfenster(punkte, "anheizen_max", 1)
    assert zuendung + 6 * 60 < anheizen < zuendung + 10 * 60
    assert not _zeitfenster(punkte, "anheizen_max", 2)
    for zyklus, start in ((1, zuendung), (2, nachlegen[0]), (3, nachlegen[1])):
        (stabil,) = _zeitfenster(punkte, "abbrand_stabil", zyklus)
        (bester,) = _zeitfenster(punkte, "bester_abbrand", zyklus)
        (glut,) = _zeitfenster(punkte, "glut", zyklus)
        assert start < stabil < start + ANSTIEG * 60
        assert stabil < bester < start + (ANSTIEG + PLATEAU + 10) * 60
        assert bester < glut
        if zyklus < 3:
            assert glut == nachlegen[zyklus - 1]
    # Letzter Zyklus: Abfall halb so steil wie am Anfang, bei etwa 135 °C
    glut = _zeitfenster(punkte, "glut", 3)[0]
    t_glut = next(p.t for p in proben if p.zeit == glut)
    assert 120 < t_glut < 150
    (abk,) = _zeitfenster(punkte, "abkuehlen", 3)
    t_abk = next(p.t for p in proben if p.zeit == abk)
    assert abk > glut and t_abk == pytest.approx((t_glut + 50) / 2, abs=5)


def test_ohne_feuer_keine_punkte():
    proben = [Probe(T0 + s, 20.0) for s in range(0, 600, TAKT)]
    assert punkte_vorschlagen(proben, [Ereignis(KEIN_FEUER, T0 + 600, 0)]) == []
    assert punkte_vorschlagen([], []) == []


def test_ohne_abkuehldaten_kein_abkuehlpunkt():
    proben, _ = abbrand(zyklen=1, nachlauf_min=20)
    punkte = punkte_vorschlagen(proben, ereignisse_ermitteln(proben, 0, True))
    assert not [p for p in punkte if p.art == "abkuehlen"]
    assert [p for p in punkte if p.art == "bester_abbrand"]


# ---- Werte ----

def _vorschlaege(punkte, proben, **kw):
    return {v.feld: v for v in werte_ableiten(proben, punkte, Parameter(), GRENZEN, **kw)}


def test_werte_aus_den_vorgeschlagenen_punkten():
    proben, _ = abbrand(zyklen=3)
    ev = ereignisse_ermitteln(proben, 2, True)
    punkte = punkte_vorschlagen(proben, ev)
    v = _vorschlaege(punkte, proben, ereignisse=ev)
    assert set(v) == {"t_kaltstart_ende", "t_abbrandstart", "gradient_start", "t_ziel", "t_ziel_eco",
                      "t_ziel_normal", "t_ziel_power", "ausbrand_abfall", "t_glut", "t_abkuehlen"}
    for feld, vorschlag in v.items():
        minimum, maximum, schritt = GRENZEN[feld]
        assert minimum <= vorschlag.wert <= maximum
        assert vorschlag.wert / schritt == pytest.approx(round(vorschlag.wert / schritt))
        assert vorschlag.alt == {"t_ziel_eco": 220, "t_ziel_normal": 250, "t_ziel_power": 270}.get(
            feld, getattr(Parameter(), feld, None))
        assert "Achtung" not in vorschlag.begruendung
    assert v["t_ziel"].wert == pytest.approx(SPITZE, abs=5) and v["t_ziel"].punkte == 3
    assert v["t_ziel_normal"].wert == v["t_ziel"].wert
    assert v["t_ziel_power"].wert - v["t_ziel_normal"].wert == 20
    assert v["t_ziel_normal"].wert - v["t_ziel_eco"].wert == 30
    assert v["t_abbrandstart"].punkte == 3 and v["t_kaltstart_ende"].punkte == 1
    assert v["t_glut"].wert < v["t_abbrandstart"].wert < v["t_ziel"].wert
    assert v["t_abkuehlen"].wert < v["t_glut"].wert
    # Schwelle Anheizen → Abbrand deutlich unter dem Ziel: 65 % des Anstiegs
    assert v["t_abbrandstart"].wert <= v["t_ziel"].wert - 30
    # Zyklus 1: 20 + 0,65 · 230 ≈ 170 °C, Nachlegen: 150 + 0,65 · 100 ≈ 215 °C
    assert v["t_abbrandstart"].wert == pytest.approx((170 + 215 + 215) / 3, abs=10)
    assert 1 <= v["gradient_start"].wert <= 10 and v["gradient_start"].punkte == 3
    assert "ersten 5 min" in v["gradient_start"].begruendung
    # ohne Ereignisse ersatzweise ab 20 K über dem Tiefstwert, ähnliches Ergebnis
    ersatz = _vorschlaege(punkte, proben)["gradient_start"].wert
    assert ersatz == pytest.approx(v["gradient_start"].wert, abs=1.5)
    assert v["t_abbrandstart"].begruendung.startswith("Mittel aus 3 Zyklen: ")
    assert "„Abbrand stabil“" in v["t_abbrandstart"].begruendung
    assert v["t_kaltstart_ende"].begruendung.startswith("Aus 1 Zyklus: ")


def flach(werte, dauer_s=3600):
    """Konstante Stufen: je Stufe (beginn_s, t)."""
    proben = []
    for s in range(0, dauer_s, TAKT):
        t = [w for b, w in werte if s >= b][-1]
        proben.append(Probe(T0 + s, t))
    return proben


def test_werte_mitteln_runden_und_begrenzen():
    proben = flach([(0, 241.0), (1200, 247.0), (2400, 460.0)])
    punkte = [Punkt("bester_abbrand", T0 + 600, 1, False), Punkt("bester_abbrand", T0 + 1800, 2, False),
              Punkt("zu_heiss", T0 + 3000, 3, False), Punkt("glut", T0 + 2000, 2, False)]
    v = _vorschlaege(punkte, proben, ziele_alt={"t_ziel_eco": 200, "t_ziel_normal": 230, "t_ziel_power": 495})
    assert v["t_ziel"].wert == 245 and v["t_ziel"].punkte == 2      # Mittel 244 → 245
    assert "Mittel aus 2 Zyklen: 244 °C" in v["t_ziel"].begruendung
    assert v["t_ziel_eco"].wert == 215 and v["t_ziel_eco"].alt == 200
    assert v["t_ziel_power"].wert == 500 and "begrenzt" in v["t_ziel_power"].begruendung
    assert v["t_uebertemperatur"].wert == 460 and v["t_uebertemperatur"].punkte == 1
    # t_glut 247 °C: Mittel → Warnung, weil nicht unter Abbrand stabil (bisher 160) und Ziel
    assert "Achtung" in v["t_glut"].begruendung
    assert v["t_glut"].wert == 245


def test_grenzen_greifen():
    proben = flach([(0, 30.0)])
    v = _vorschlaege([Punkt("abkuehlen", T0 + 600, 1)], proben)
    assert v["t_abkuehlen"].wert == 40 and "begrenzt" in v["t_abkuehlen"].begruendung


def test_fehlende_punkte_kein_vorschlag():
    proben = flach([(0, 140.0)])
    v = _vorschlaege([Punkt("glut", T0 + 600, 1)], proben)
    assert set(v) == {"t_glut"}             # kein Maximum über der Glut → kein Ausbrand-Abfall
    assert _vorschlaege([], proben) == {}
    # Punkt außerhalb der Daten trägt nichts bei
    assert _vorschlaege([Punkt("glut", T0 + 99999, 1)], proben) == {}


def test_warnung_bei_unplausibler_reihenfolge():
    proben = flach([(0, 200.0), (1200, 150.0), (2400, 170.0)])
    punkte = [Punkt("abbrand_stabil", T0 + 600, 1), Punkt("bester_abbrand", T0 + 1800, 1),
              Punkt("abkuehlen", T0 + 3000, 1)]
    v = _vorschlaege(punkte, proben)
    # Abbrand stabil 200 > Ziel 150, Abkühlen 170 > bisherige Glut 130
    assert "Achtung" in v["t_abbrandstart"].begruendung and "Achtung" in v["t_ziel"].begruendung
    assert "Achtung" in v["t_abkuehlen"].begruendung
    assert v["t_abbrandstart"].wert == 200      # nicht still korrigiert


def test_gradient_start_frueh_und_hart_begrenzt():
    # 1 °C/s = 60 K/min ab dem Feuer: ein Drittel wären 20, begrenzt auf 10
    steil = [Probe(T0 + s, 20.0 + max(0, s - 600)) for s in range(0, 1500, TAKT)]
    v = _vorschlaege([Punkt("abbrand_stabil", T0 + 1200, 1)], steil, ereignisse=[Ereignis(FEUER, T0 + 600, 1)])
    assert v["gradient_start"].wert == 10 and "begrenzt" in v["gradient_start"].begruendung
    # 1,5 K/min: ein Drittel 0,5, hart auf 1 angehoben
    flau = [Probe(T0 + s, 20.0 + max(0, s - 600) * 0.025) for s in range(0, 3000, TAKT)]
    v = _vorschlaege([Punkt("abbrand_stabil", T0 + 2400, 1)], flau, ereignisse=[Ereignis(FEUER, T0 + 600, 1)])
    assert v["gradient_start"].wert == 1
    # Anfänge kommen aus den Ereignissen, nicht aus der Zahl der Punkte
    v = _vorschlaege([], steil, ereignisse=[Ereignis(FEUER, T0 + 600, 1)])
    assert set(v) == {"gradient_start"}


def test_ausbrand_abfall_aus_maximum_und_glut():
    proben = flach([(0, 250.0), (1800, 150.0)])
    v = _vorschlaege([Punkt("glut", T0 + 2400, 1)], proben)
    assert v["ausbrand_abfall"].wert == 30      # 30 % von 100 K


# ---- Echter Verlauf ----

def test_echter_verlauf_vom_08_11():
    roh = json.loads(FIXTUR.read_text())
    proben = [Probe(x["jetzt"], x["t"], x.get("g")) for x in roh]
    for tuer in (False, True):
        ev = ereignisse_ermitteln(proben, 1, tuer)
        assert ev and ev[0].art == FEUER
        assert len(ev) == len({(e.art, e.zeit) for e in ev})
        assert all(a.zeit <= b.zeit for a, b in zip(ev, ev[1:]) if b.art != NACHLEGEN)
    ev = ereignisse_ermitteln(proben, 1, False)
    # Am Ende wird bei 119 °C nachgelegt (Sprung auf 240 °C), das muss die Umkehr finden
    nach = nur(ev, NACHLEGEN)
    assert len(nach) == 1 and nach[0].zeit > proben[0].zeit + 2400
    punkte = punkte_vorschlagen(proben, ev)
    werte_ableiten(proben, punkte, Parameter(), GRENZEN)


# ---- Nachbesserungen: Drosseln, Ausfall des Türkontakts, kein Warmwerden, Prellen ----

def test_drosseln_von_hand_im_abbrand_ist_kein_nachlegen():
    """Ohne Tür: im Plateau 4 min von Hand gedrosselt (250 → 215 °C), dann wieder auf: kein Nachlegen."""
    proben, _ = abbrand(zyklen=1, tuer=False, wellen=0.0)
    feuer = ereignisse_ermitteln(proben, 1, False)[0].zeit
    beginn = feuer + 25 * 60
    geaendert = []
    for p in proben:
        d = p.zeit - beginn
        if 0 <= d < 240:
            t = p.t - 35 * d / 240
        elif 240 <= d < 420:
            t = p.t - 35 * (1 - (d - 240) / 180)
        else:
            t = p.t
        geaendert.append(Probe(p.zeit, t))
    assert arten(ereignisse_ermitteln(geaendert, 1, False)) == [FEUER, KALT]


def test_umkehr_ohne_tuer_beachtet_die_sperre():
    """Ein Einbruch mit Wiederanstieg gleich nach dem Feuer zählt ohne Tür nicht als Nachlegen."""
    z = Zyklenzaehler(1, False)
    zeit, t = T0, 20.0
    ev = []
    for t_neu in [20.0] * 24 + [20 + 8 * k for k in range(1, 26)] + [220 - 4 * k for k in range(1, 30)] \
            + [104 + 6 * k for k in range(1, 15)]:
        ev.append(z.schritt(Probe(zeit, t_neu)))
        zeit += 10
    assert arten([e for e in ev if e]) == [FEUER]
    assert zeit - T0 < em.SPERRE_S + 400       # alles innerhalb der Sperre nach dem Feuer


def test_tuerkontakt_faellt_aus_dann_gradientenumkehr():
    proben, nachlegen = abbrand(zyklen=3)
    feuer = ereignisse_ermitteln(proben, 2, True)[0].zeit
    ohne = [Probe(p.zeit, p.t, None, None) if p.zeit > feuer + 600 else p for p in proben]
    z = Zyklenzaehler(2, True)
    ev = [e for e in (z.schritt(p) for p in ohne) if e]
    assert z.tuer_ausgefallen and not z.tuerkontakt
    assert arten(ev) == [FEUER, NACHLEGEN, NACHLEGEN, FERTIG, KALT]
    for e, soll in zip(nur(ev, NACHLEGEN), nachlegen):
        assert abs(e.zeit - soll) <= 60
    # Eine kurze Lücke unter 2 min schaltet nicht um
    kurz = [Probe(p.zeit, p.t, None, None) if feuer + 600 < p.zeit < feuer + 650 else p for p in proben]
    z = Zyklenzaehler(2, True)
    for p in kurz:
        z.schritt(p)
    assert not z.tuer_ausgefallen and z.tuerkontakt


def test_feuer_ohne_warmwerden_endet_nach_60_min():
    proben = [Probe(T0 + s, 20.0 + min(s, 600) * 0.04, tuer_offen=False) for s in range(0, 9000, TAKT)]
    z = Zyklenzaehler(2, True, kein_feuer_s=3600)
    ev = [e for e in (z.schritt(p) for p in proben) if e]
    assert arten(ev) == [FEUER, KEIN_FEUER] and z.beendet
    assert ev[1].zeit == pytest.approx(ev[0].zeit + 3600, abs=TAKT)


def test_fertig_erst_nach_30_s_tuer_zu():
    proben, nachlegen = abbrand(zyklen=2)
    # nach dem Nachlegen: 20 s zu, 10 s offen, dann zu
    geaendert = []
    for p in proben:
        d = p.zeit - nachlegen[0]
        offen = p.tuer_offen
        if TUER_DAUER_S <= d < TUER_DAUER_S + 20:
            offen = False
        elif TUER_DAUER_S + 20 <= d < TUER_DAUER_S + 30:
            offen = True
        geaendert.append(Probe(p.zeit, p.t, None, offen))
    ev = ereignisse_ermitteln(geaendert, 1, True)
    assert nur(ev, FERTIG)[0].zeit == nachlegen[0] + TUER_DAUER_S + 30 + em.TUER_ZU_S
    assert len(nur(ev, NACHLEGEN)) == 1
