"""Regelstrategie 2.0: PI mit Schwingungswächter und gerichtetem Vorhalt (nur Regelstand neu).

Herleitung (Auswertung 14.–16.11.2026, 20 Abbrände im Betrieb): Der Weg Klappe → Abgas hat 35–80 s Totzeit
und je nach Holz und Abbrandstand 0,7–2,7 K/%. Ein fest eingestellter Regler ist für die steile Strecke zu
scharf (Dauerschwingung um 3 min) oder für die flache zu träge.

Vorhalt (0.5.4, Auswertung 16.11. mit dem eigenen Fühler, Vergleichsbank mit Antrieb 150 s, 2 % Ansprech-
schwelle, Fühler 5 s / 1 K, Abfall langsamer als Anstieg): Der klassische D-Anteil auf dem 2-min-Gradienten
hinkt etwa 1 min nach und wirkt an der Schwingfrequenz als zusätzliche Verstärkung (Bank: bis 74 K Schwingung).
Der Vorhalt der 2.0 arbeitet deshalb anders:
- auf der Ausgleichsgeraden über 60 s (hinkt etwa 30 s nach),
- nur, wenn sich die Temperatur vom Ziel entfernt (unter dem Ziel fallend, über dem Ziel steigend). Läuft sie
  aufs Ziel zu, regeln P und I allein; ein Bremsen dort verlängert nur das Unterschwingen,
- beim Fallen stärker (Klappe auf geht schnell und ist sicher, der Fühler über der Wassertasche zeigt den
  Abfall verzögert) als beim Steigen (Schließen ist ohnehin auf die Schließrate begrenzt),
- mit der Dämpfung im Quadrat: Bei Schwingung tritt er zuerst zurück.
Vorgabe 0,5 fallend / 0,25 steigend. Bank, Median über 54 Strecken: Unterschwingen 10,9 → 9,4 K, Schwingung
3,1 → 2,5 K, Klappenweg 1,5 → 1,7 %/min. Im Regelkreismodell (bis 3,5 K/%, LUC-Fühler) bleibt es überall ruhig;
0,75 beim Fallen schwingt dort auf der steilsten Strecke (6–8 K).

Langsamer Antrieb: Im Abbrand fährt die 2.0 höchstens wenige % je 30 s, der Antrieb schafft 20 %/30 s. Begrenzend
ist nur die Schließrate nach dem Anheizen. Die Ansprechschwelle des Antriebs (Ist folgt in 1,5–3-%-Stufen)
trägt in der Bank etwa 0,5 K zum Pendeln bei, sie ist nicht die Ursache.

Der Wächter beobachtet die Umkehrpunkte der Abgastemperatur (Zickzack mit 8 K Hub, Halbwellen 40–300 s,
Gedanke wie bei Hägglunds Schwingungsdetektor). Drei regelmäßige Halbwellen heißen Schwingung: Die Dämpfung
sinkt auf 70 % (Kp × Dämpfung, Tn / √Dämpfung), höchstens bis 30 %. Nach 15 min Abbrand ohne Schwingung
steigt sie um 10 %, bis wieder 100 %. Die Dämpfung bleibt über Abbrände erhalten (die App speichert sie).

Nur Standardbibliothek, keine Uhr, kein I/O.
"""
from __future__ import annotations

import math
from dataclasses import replace

from kern.signale import Werte
from kern.typen import Parameter, Zustand

STRATEGIE_KLASSISCH = 1
STRATEGIE_2 = 2

UMKEHR_K = 8.0              # so weit muss die Temperatur vom Extrem zurücklaufen (Zickzack, 8 K Hub)
HALBWELLE_S = (40.0, 300.0)  # Perioden 1,3 bis 10 min
HALBWELLEN = 3              # anderthalb Perioden
REGELMAESSIG = 2.0          # längste Halbwelle höchstens doppelt so lang wie die kürzeste
SCHRITT = 0.7
DAEMPFUNG_MIN = 0.3
ERHOLUNG_S = 900.0
ERHOLUNG = 1.1


def strategie_2(p: Parameter) -> bool:
    return round(p.regelstrategie) == STRATEGIE_2


def zuruecksetzen(z: Zustand) -> None:
    """Außerhalb des Abbrands: Beobachtung vergessen, Dämpfung behalten."""
    z.sw_extrem = z.sw_extrem_zeit = z.sw_gegen = z.sw_gegen_zeit = z.sw_beginn = z.sw_ruhig_seit = None
    z.sw_richtung = 0
    z.sw_wellen = ()


def _umkehr(z: Zustand, zeit: float) -> bool:
    """Ein Umkehrpunkt zur Zeit `zeit`. True, wenn damit eine Schwingung erkannt ist."""
    if z.sw_beginn is not None:
        dauer = zeit - z.sw_beginn
        if HALBWELLE_S[0] <= dauer <= HALBWELLE_S[1]:
            z.sw_wellen = (tuple(z.sw_wellen) + (dauer,))[-HALBWELLEN:]
        else:
            z.sw_wellen = ()
    z.sw_beginn = zeit
    return len(z.sw_wellen) >= HALBWELLEN and max(z.sw_wellen) <= REGELMAESSIG * min(z.sw_wellen)


def beobachten(z: Zustand, t: float, jetzt: float) -> bool:
    """Ein Messwert im Abbrand. Gibt True zurück, wenn die Dämpfung gerade gesenkt wurde.

    Zickzack: Ein Umkehrpunkt zählt, wenn die Temperatur danach um UMKEHR_K zurückläuft. Das ist unabhängig
    vom Trend des Abbrands (ein gleitendes Mittel hinkt dem Abfall hinterher und verpasst die Schwingung).
    """
    if z.sw_extrem is None or z.sw_extrem_zeit is None:
        z.sw_extrem = z.sw_gegen = t
        z.sw_extrem_zeit = z.sw_gegen_zeit = z.sw_ruhig_seit = jetzt
        z.sw_richtung = 0
        return False
    erkannt = False
    if z.sw_richtung == 0:
        # Noch kein Umkehrpunkt: Maximum in sw_extrem, Minimum in sw_gegen
        if t > z.sw_extrem:
            z.sw_extrem, z.sw_extrem_zeit = t, jetzt
        if z.sw_gegen is None or t < z.sw_gegen:
            z.sw_gegen, z.sw_gegen_zeit = t, jetzt
        if z.sw_extrem - t >= UMKEHR_K:
            z.sw_beginn, z.sw_richtung = z.sw_extrem_zeit, -1
            z.sw_extrem, z.sw_extrem_zeit = t, jetzt
        elif z.sw_gegen is not None and t - z.sw_gegen >= UMKEHR_K:
            z.sw_beginn, z.sw_richtung = z.sw_gegen_zeit, 1
            z.sw_extrem, z.sw_extrem_zeit = t, jetzt
    elif (t - z.sw_extrem) * z.sw_richtung > 0:
        z.sw_extrem, z.sw_extrem_zeit = t, jetzt
    elif (z.sw_extrem - t) * z.sw_richtung >= UMKEHR_K:
        erkannt = _umkehr(z, z.sw_extrem_zeit)
        z.sw_richtung = -z.sw_richtung
        z.sw_extrem, z.sw_extrem_zeit = t, jetzt
    if z.sw_wellen and z.sw_beginn is not None and jetzt - z.sw_beginn > HALBWELLE_S[1] + 60:
        z.sw_wellen = ()        # zu lange keine Umkehr: keine Schwingung
    if erkannt:
        z.daempfung = max(round(z.daempfung * SCHRITT, 3), DAEMPFUNG_MIN)
        z.sw_wellen = ()
        z.sw_ruhig_seit = z.sw_gedaempft = jetzt
    elif z.sw_ruhig_seit is None:
        z.sw_ruhig_seit = jetzt
    elif not z.sw_wellen and jetzt - z.sw_ruhig_seit >= ERHOLUNG_S:
        z.daempfung = min(round(z.daempfung * ERHOLUNG, 3), 1.0)
        z.sw_ruhig_seit = jetzt
    return erkannt


def vorhalt_2(w: Werte, p: Parameter, m: float) -> tuple[float, float]:
    """(Kd, Steigung) der Strategie 2.0. Kd ist 0, solange die Temperatur aufs Ziel zuläuft oder steht."""
    g = w.g if w.g_kurz is None else w.g_kurz
    e = w.ziel - w.t
    if e * g >= 0:
        return 0.0, g
    kd = p.regler2_vorhaltung_fallend if g < 0 else p.regler2_vorhaltung_steigend
    return max(kd, 0.0) * m * m, g


def werte_2(w: Werte, p: Parameter, z: Zustand) -> Werte:
    """Die Reglerwerte der Strategie 2.0 in `Werte`: Kp und Tn gedämpft, gerichteter Vorhalt."""
    m = min(max(z.daempfung, DAEMPFUNG_MIN), 1.0)
    kd, g = vorhalt_2(w, p, m)
    return replace(w, kp=p.regler2_verstaerkung * m,
                   tn=max(p.regler2_nachstellzeit, 0.5) / math.sqrt(m), kd=kd, g=g)
