"""Matrix, Entprellung, Sendefehler und die leere Vorgabe."""
import asyncio
import json

import pytest

from anbindung.benachrichtigung import (ARTEN, Benachrichtiger, ZuordnungFehler, geraete_aus_diensten,
                                        zuordnung_pruefen)
from anbindung.einstellungen import EinstellungsFehler, Speicher

DRINGEND = {"push": {"interruption-level": "time-sensitive"}}


class Uhr:
    def __init__(self):
        self.jetzt = 1000.0

    def __call__(self):
        return self.jetzt


class Sender:
    def __init__(self):
        self.aufrufe = []
        self.fehler = None
        self.haengt = False

    async def __call__(self, dienst, titel, text, daten):
        if self.haengt:
            await asyncio.sleep(3600)
        if self.fehler:
            raise self.fehler
        self.aufrufe.append((dienst, titel, text, daten))


def bauen(zuordnung, **weiteres):
    uhr, sender = Uhr(), Sender()
    return Benachrichtiger(lambda: zuordnung, sender, uhr, **weiteres), sender, uhr


def lauf(ablauf):
    asyncio.run(ablauf())


def test_arten_sind_vollstaendig():
    assert [a.schluessel for a in ARTEN] == ["nachlegen", "stoerung", "reinigung", "betrieb",
                                             "wasser_hinweis", "wasser_warnung", "wasser_alarm"]
    assert all(a.titel for a in ARTEN)
    assert {a.schluessel for a in ARTEN if a.dringend} == {"stoerung", "wasser_alarm", "betrieb"}


def test_leere_vorgabe_sendet_nichts(tmp_path):
    async def ablauf():
        s = Speicher(tmp_path / "e.json")
        assert s.benachrichtigungen == {}
        b, sender, _ = bauen(s.benachrichtigungen)
        for art in ARTEN:
            assert b.melden(art.schluessel, "Text") == []
        await b.abwarten()
        assert sender.aufrufe == []
    lauf(ablauf)


def test_nur_zugeordnete_geraete_je_art():
    async def ablauf():
        b, sender, _ = bauen({"nachlegen": ["notify.a"], "reinigung": ["notify.a", "notify.b"]})
        assert b.melden("nachlegen", "Glut") == ["notify.a"]
        assert b.melden("reinigung", "Reinigen") == ["notify.a", "notify.b"]
        assert b.melden("stoerung", "Fehler") == []
        await b.abwarten()
        assert [(d, t) for d, _, t, _ in sender.aufrufe] == [("notify.a", "Glut"), ("notify.a", "Reinigen"), ("notify.b", "Reinigen")]
    lauf(ablauf)


def test_dringende_arten_senden_time_sensitive_die_anderen_nicht():
    async def ablauf():
        alle = {a.schluessel: ["notify.a"] for a in ARTEN}
        b, sender, _ = bauen(alle)
        for a in ARTEN:
            b.melden(a.schluessel, "x")
        await b.abwarten()
        daten = {art: d for (_, titel, _, d), art in zip(sender.aufrufe, [a.schluessel for a in ARTEN])}
        assert {k for k, v in daten.items() if v == DRINGEND} == {"stoerung", "wasser_alarm", "betrieb"}
        assert all(v is None for k, v in daten.items() if k not in ("stoerung", "wasser_alarm", "betrieb"))
    lauf(ablauf)


def test_gleiche_art_und_text_hoechstens_alle_10_minuten():
    async def ablauf():
        b, sender, uhr = bauen({"nachlegen": ["notify.a"]})
        assert b.melden("nachlegen", "Glut")
        uhr.jetzt += 599
        assert b.melden("nachlegen", "Glut") == []
        assert b.melden("nachlegen", "Anderer Text")           # anderer Text zählt extra
        uhr.jetzt += 1
        assert b.melden("nachlegen", "Glut")
        await b.abwarten()
        assert len(sender.aufrufe) == 3
    lauf(ablauf)


def test_ohne_geraet_wird_nicht_entprellt():
    async def ablauf():
        zuordnung = {}
        b, sender, uhr = bauen(zuordnung)
        b.melden("nachlegen", "Glut")
        zuordnung["nachlegen"] = ["notify.a"]
        assert b.melden("nachlegen", "Glut") == ["notify.a"]
        await b.abwarten()
    lauf(ablauf)


def test_sendefehler_wird_geloggt_und_stoert_nicht(caplog):
    async def ablauf():
        b, sender, _ = bauen({"stoerung": ["notify.a", "notify.b"]})
        sender.fehler = RuntimeError("kaputt")
        assert b.melden("stoerung", "Fehler")        # wirft nicht
        await b.abwarten()
        assert sender.aufrufe == []
        sender.fehler = None
        b.melden("stoerung", "Fehler 2")
        await b.abwarten()
        assert [d for d, *_ in sender.aufrufe] == ["notify.a", "notify.b"]
    lauf(ablauf)
    assert caplog.text.count("nicht gesendet") == 2     # beide Geräte, eines nach dem anderen


def test_haengender_versand_blockiert_nicht_und_hat_zeitlimit():
    async def ablauf():
        b, sender, _ = bauen({"stoerung": ["notify.a"]}, timeout_s=0.05)
        sender.haengt = True
        b.melden("stoerung", "Fehler")
        await asyncio.sleep(0)             # melden ist schon zurückgekehrt, der Versand läuft getrennt
        await asyncio.wait_for(b.abwarten(), 2)    # und endet am Zeitlimit
    lauf(ablauf)


def test_ungueltiger_dienst_in_der_zuordnung_wird_nicht_gesendet():
    async def ablauf():
        b, sender, _ = bauen({"stoerung": ["cover.open_cover", "notify.persistent_notification", "notify.ok"]})
        assert b.melden("stoerung", "x") == ["notify.ok"]
        await b.abwarten()
    lauf(ablauf)


def test_unbekannte_art_wird_ignoriert():
    async def ablauf():
        b, sender, _ = bauen({"gibt_es_nicht": ["notify.a"]})
        assert b.melden("gibt_es_nicht", "x") == []
    lauf(ablauf)


@pytest.mark.parametrize("roh", [
    [], {"unbekannt": ["notify.a"]}, {"nachlegen": "notify.a"}, {"nachlegen": ["light.turn_on"]},
    {"nachlegen": ["notify.send_message"]}, {"nachlegen": ["notify.persistent_notification"]},
    {"nachlegen": ["notify.A"]}, {"nachlegen": [None]},
])
def test_zuordnung_pruefen_lehnt_ab(roh):
    with pytest.raises(ZuordnungFehler):
        zuordnung_pruefen(roh)


def test_zuordnung_pruefen_bereinigt():
    assert zuordnung_pruefen({"nachlegen": ["notify.a", "notify.a"], "stoerung": []}) == {"nachlegen": ["notify.a"]}


def test_matrix_wird_gespeichert_und_ueberlebt_neustart(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.benachrichtigungen_setzen({"nachlegen": ["notify.a"], "stoerung": ["notify.a", "notify.b"]})
    assert json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))["benachrichtigungen"]["stoerung"] == ["notify.a", "notify.b"]
    assert Speicher(tmp_path / "e.json").benachrichtigungen == {"nachlegen": ["notify.a"], "stoerung": ["notify.a", "notify.b"]}
    with pytest.raises(EinstellungsFehler):
        s.benachrichtigungen_setzen({"nachlegen": ["script.x"]})
    assert s.benachrichtigungen["nachlegen"] == ["notify.a"]


def test_kaputte_datei_sendet_nichts(tmp_path):
    s = Speicher(tmp_path / "e.json")
    s.daten["benachrichtigungen"] = {"nachlegen": ["script.turn_on"]}
    assert s.benachrichtigungen == {}


def test_geraeteliste_mobile_app_zuerst_ohne_send_message():
    dienste = [
        {"domain": "light", "services": {"turn_on": {}}},
        {"domain": "notify", "services": {"send_message": {}, "persistent_notification": {}, "zebra": {},
                                           "mobile_app_handy_b": {}, "mobile_app_handy_a": {}, "alpha": {}}},
    ]
    geraete = geraete_aus_diensten(dienste)
    assert [g["dienst"] for g in geraete] == ["notify.mobile_app_handy_a", "notify.mobile_app_handy_b",
                                              "notify.alpha", "notify.zebra"]
    assert geraete[0]["name"] == "Handy A" and geraete[0]["handy"] and not geraete[2]["handy"]
