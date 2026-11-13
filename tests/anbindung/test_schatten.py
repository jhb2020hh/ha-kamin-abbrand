import asyncio
import json
from datetime import datetime, timedelta

from anbindung.schatten import Schatten, Verlauf
from kern.typen import NEU, PARITAET, Eingaben, Ergebnis, Meldung, Zustand

JETZT = datetime(2026, 11, 6, 23, 59, 50).timestamp()


def ergebnis(klappe=50, phase="Abbrand"):
    return Ergebnis(zustand=Zustand(phase=phase, i_anteil=57.9, klappe_soll=klappe), klappe=klappe)


class Schreiber:
    def __init__(self):
        self.aufrufe = []
        self.fehler = None

    async def __call__(self, eid, wert, attribute):
        if self.fehler:
            raise self.fehler
        self.aufrufe.append((eid, wert))


def test_namen_und_werte_je_regelstand():
    async def ablauf():
        schreiber = Schreiber()
        schatten = Schatten(schreiber)
        await schatten.ergebnis(PARITAET, ergebnis(), JETZT)
        await schatten.ergebnis(NEU, ergebnis(30, "Glut"), JETZT)
        await schatten.status("ok", JETZT)
        assert dict(schreiber.aufrufe) == {
            "sensor.kamin_app_p_phase": "Abbrand",
            "sensor.kamin_app_p_klappe_soll": "50",
            "sensor.kamin_app_p_i_anteil": "57.90",
            "sensor.kamin_app_p_signal": "frisch",
            "sensor.kamin_app_n_phase": "Glut",
            "sensor.kamin_app_n_klappe_soll": "30",
            "sensor.kamin_app_n_i_anteil": "57.90",
            "sensor.kamin_app_n_signal": "frisch",
            "sensor.kamin_app_status": "ok",
        }

    asyncio.run(ablauf())


def test_schreibt_nur_bei_aenderung_oder_nach_60_s():
    async def ablauf():
        schreiber = Schreiber()
        schatten = Schatten(schreiber)
        await schatten.status("ok", JETZT)
        await schatten.status("ok", JETZT + 59)
        assert len(schreiber.aufrufe) == 1
        await schatten.status("fehler", JETZT + 59.5)
        assert len(schreiber.aufrufe) == 2
        await schatten.status("fehler", JETZT + 119.5)
        assert len(schreiber.aufrufe) == 3

    asyncio.run(ablauf())


def test_fehlgeschlagener_schreibvorgang_wird_wiederholt():
    async def ablauf():
        schreiber = Schreiber()
        schatten = Schatten(schreiber)
        schreiber.fehler = OSError("weg")
        await schatten.status("ok", JETZT)
        schreiber.fehler = None
        await schatten.status("ok", JETZT + 1)
        assert schreiber.aufrufe == [("sensor.kamin_app_status", "ok")]

    asyncio.run(ablauf())


def test_vergessen_schreibt_alles_neu():
    async def ablauf():
        schreiber = Schreiber()
        schatten = Schatten(schreiber)
        await schatten.status("ok", JETZT)
        schatten.vergessen()
        await schatten.status("ok", JETZT + 1)
        assert len(schreiber.aufrufe) == 2

    asyncio.run(ablauf())


def zeilen(pfad):
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()]


def test_verlauf_eine_zeile_je_lauf_mit_eingaben_und_ergebnis(tmp_path):
    verlauf = Verlauf(tmp_path)
    erg = ergebnis()
    erg.meldungen.append(Meldung("Störung", "Tür offen"))
    erg.uebergang = ("Glut", "Abbrand")
    verlauf.schreiben(PARITAET, Eingaben(jetzt=JETZT, t=None), erg)
    verlauf.schreiben(NEU, Eingaben(jetzt=JETZT, t=100.0), fehler="ValueError()")
    erste, zweite = zeilen(tmp_path / "verlauf-2026-11-06.jsonl")
    assert erste["regelstand"] == "paritaet"
    assert erste["eingaben"]["t"] is None
    assert erste["ergebnis"]["zustand"]["phase"] == "Abbrand"
    assert erste["ergebnis"]["meldungen"][0]["titel"] == "Störung"
    assert zweite["ergebnis"] is None and zweite["fehler"] == "ValueError()"


def test_verlauf_rotiert_um_mitternacht(tmp_path):
    verlauf = Verlauf(tmp_path)
    verlauf.schreiben(PARITAET, Eingaben(jetzt=JETZT, t=1.0), ergebnis())
    verlauf.schreiben(PARITAET, Eingaben(jetzt=JETZT + 20, t=2.0), ergebnis())
    assert len(zeilen(tmp_path / "verlauf-2026-11-06.jsonl")) == 1
    assert len(zeilen(tmp_path / "verlauf-2026-11-07.jsonl")) == 1


def test_verlauf_loescht_nach_30_tagen_und_nur_eigene_dateien(tmp_path):
    heute = datetime.fromtimestamp(JETZT).date()
    namen = {
        tage: tmp_path / f"verlauf-{(heute - timedelta(days=tage)).isoformat()}.jsonl"
        for tage in (1, 30, 31, 90)
    }
    for pfad in namen.values():
        pfad.write_text("{}\n")
    fremd = [tmp_path / "verlauf-kaputt.jsonl", tmp_path / "options.json"]
    for pfad in fremd:
        pfad.write_text("x")
    Verlauf(tmp_path).schreiben(PARITAET, Eingaben(jetzt=JETZT, t=1.0), ergebnis())
    assert namen[1].exists() and namen[30].exists()
    assert not namen[31].exists() and not namen[90].exists()
    assert all(p.exists() for p in fremd)


def test_verlauf_ueberlebt_plattenfehler(tmp_path):
    Verlauf(tmp_path / "fehlt").schreiben(PARITAET, Eingaben(jetzt=JETZT, t=1.0), ergebnis())


def test_sensor_reinigung_zustaende_und_attribute():
    from kern import wartung

    async def ablauf():
        aufrufe = []

        async def schreiber(eid, wert, attribute):
            aufrufe.append((eid, wert, attribute))

        schatten = Schatten(schreiber)
        stand = wartung.Stand(basis_s=0)
        await schatten.reinigung(wartung.pruefen(40 * 3600, stand, 100), None, JETZT)
        await schatten.reinigung(wartung.pruefen(100 * 3600, stand, 100), JETZT - 86400, JETZT + 1)
        await schatten.reinigung(wartung.pruefen(100 * 3600, stand, 0), None, JETZT + 2)
        rest = [(w, a) for e, w, a in aufrufe if e == "sensor.kamin_app_wt_reinigung_rest"]
        aufrufe = [x for x in aufrufe if x[0] != "sensor.kamin_app_wt_reinigung_rest"]
        assert [w for w, _ in rest] == ["60.0", "0.0", "unknown"]
        assert rest[0][1]["unit_of_measurement"] == "h" and rest[0][1]["device_class"] == "duration"
        assert [(e, w) for e, w, _ in aufrufe] == [("sensor.kamin_app_wt_reinigung", "ok"),
                                                   ("sensor.kamin_app_wt_reinigung", "faellig"),
                                                   ("sensor.kamin_app_wt_reinigung", "aus")]
        ok, faellig, aus = (a for _, _, a in aufrufe)
        assert ok["reststunden"] == 60 and ok["intervall_stunden"] == 100 and ok["letzte_reinigung"] is None
        assert faellig["reststunden"] == 0 and faellig["letzte_reinigung"].startswith("2026-11-05")
        assert "reststunden" not in aus

    asyncio.run(ablauf())
