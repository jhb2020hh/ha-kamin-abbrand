"""Nachlegen im Abbrand (Issue #4) in der App: Auflage, Schürsperre, Briketts, gesicherter Zustand."""
import json

from anbindung.betrieb import zustand_laden, zustand_sichern
from kern.phasen import ZURUECK_IN_ABBRAND
from kern.typen import ABBRAND, BRIKETTS, PARITAET, TUER_OFFEN, Ergebnis, Zustand
from test_buchfuehrung_app import auflagen, einrichten
from test_push import ablauf_mit, lauf

T0 = 1_800_000_000.0


class NachlegenSkript:
    """Meldet bei jedem Schritt die Rückkehr Tür offen → Abbrand. kurz=True: nur geschaut."""
    def __init__(self, zustand, kurz):
        self.zustand, self.kurz = zustand, kurz

    def schritt(self, eingaben):
        z = self.zustand
        z.phase = ABBRAND
        if not self.kurz:
            z.nachgelegt_seit, z.nachlegen_anzahl = float(int(eingaben.jetzt)), z.nachlegen_anzahl + 1
        return Ergebnis(zustand=z, klappe=50, regel=ZURUECK_IN_ABBRAND, uebergang=(TUER_OFFEN, ABBRAND))


def fabrik(kurz=False):
    def bauen(parameter, zustand, regelstand, takt_s):
        return NachlegenSkript(zustand, kurz)
    return bauen


def mit_tuer(app, s, zeit):
    s.rollen_setzen(s.rollen | {"tuer": "binary_sensor.tuer"}, True)
    app._uhr = lambda: zeit[0]


def test_nachlegen_im_abbrand_bucht_eine_auflage():
    async def p(app, ha, s):
        einrichten(s)
        zeit = [T0]
        mit_tuer(app, s, zeit)
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1
        assert any("Nachgelegt im Abbrand" in m["text"] for m in app.meldungen)
    ablauf_mit(p, fabrik(), {})


def test_schuersperre_gilt_auch_im_abbrand():
    async def p(app, ha, s):
        einrichten(s)
        zeit = [T0]
        mit_tuer(app, s, zeit)
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1
        zeit[0] += 300
        app._tuer_merken(True, zeit[0])                 # 5 min nach der Auflage: geschürt
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 1
        zeit[0] += 600
        app._tuer_merken(True, zeit[0])
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 2
    ablauf_mit(p, fabrik(), {})


def test_nur_geschaut_bucht_keine_auflage():
    async def p(app, ha, s):
        einrichten(s)
        zeit = [T0]
        mit_tuer(app, s, zeit)
        await lauf(app, (PARITAET,))
        assert auflagen(app) == 0
    ablauf_mit(p, fabrik(kurz=True), {})


def test_nachlegen_im_abbrand_mit_briketts_bucht_briketts():
    async def p(app, ha, s):
        einrichten(s)
        app.brennstoff_setzen(BRIKETTS)
        zeit = [T0]
        mit_tuer(app, s, zeit)
        await lauf(app, (PARITAET,))
        zeile = next(iter(app.buchfuehrung.tage.values()))
        assert zeile["brikett_auflagen"] == 1 and zeile["auflagen"] == 1
    ablauf_mit(p, fabrik(), {})


def test_gesicherter_zustand_ohne_neue_felder_laedt(tmp_path):
    pfad = tmp_path / "zustand.json"
    zustand_sichern(pfad, Zustand(phase=ABBRAND, phase_seit=T0 - 600), T0)
    daten = json.loads(pfad.read_text())
    for feld in ("tuer_aus_abbrand", "abbrand_seit", "nachgelegt_seit", "nachlegen_anzahl"):
        del daten["zustand"][feld]
    pfad.write_text(json.dumps(daten))
    z = zustand_laden(pfad, T0 + 10)
    assert z is not None and z.phase == ABBRAND
    assert (z.tuer_aus_abbrand, z.abbrand_seit, z.nachgelegt_seit, z.nachlegen_anzahl) == (False, None, None, 0)


def test_gesicherter_zustand_behaelt_den_merker(tmp_path):
    pfad = tmp_path / "zustand.json"
    zustand_sichern(pfad, Zustand(phase=TUER_OFFEN, phase_seit=T0 - 30, tuer_aus_abbrand=True,
                                  abbrand_seit=T0 - 3600, nachgelegt_seit=T0 - 1200, nachlegen_anzahl=1), T0)
    z = zustand_laden(pfad, T0 + 10)
    assert (z.tuer_aus_abbrand, z.abbrand_seit, z.nachlegen_anzahl) == (True, T0 - 3600, 1)
