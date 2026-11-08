"""Ersatz für kern.steuerung, damit die Anbindung ohne den Kern testbar ist."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from kern.typen import Eingaben, Ergebnis, Meldung, Parameter, Zustand


class StubSteuerung:
    def __init__(self, parameter: Parameter, zustand: Zustand, regelstand: str, takt_s: float,
                 verhalten: Optional[Callable[[Eingaben], None]] = None) -> None:
        self.parameter = parameter
        self.zustand = zustand
        self.regelstand = regelstand
        self.takt_s = takt_s
        self.verhalten = verhalten
        self.laeufe: list[Eingaben] = []

    def schritt(self, eingaben: Eingaben) -> Ergebnis:
        self.laeufe.append(eingaben)
        if self.verhalten is not None:
            self.verhalten(eingaben)
        self.zustand = replace(self.zustand, klappe_soll=self.zustand.klappe_soll + 1)
        return Ergebnis(
            zustand=self.zustand, klappe=self.zustand.klappe_soll,
            meldungen=[Meldung("Titel", "Text")], aktionen=["sniffer_restart", "wartung_aus"],
        )


class StubFabrik:
    """Merkt sich alle gebauten Instanzen. `neu_angeboten=False` bildet den Kern ohne neu nach."""

    def __init__(self, neu_angeboten: bool = True,
                 verhalten: Optional[Callable[[Eingaben], None]] = None) -> None:
        self.neu_angeboten = neu_angeboten
        self.verhalten = verhalten
        self.gebaut: list[StubSteuerung] = []

    def __call__(self, parameter: Parameter, zustand: Zustand, regelstand: str, takt_s: float) -> StubSteuerung:
        if regelstand == "neu" and not self.neu_angeboten:
            raise NotImplementedError
        steuerung = StubSteuerung(parameter, zustand, regelstand, takt_s, self.verhalten)
        self.gebaut.append(steuerung)
        return steuerung

    def instanz(self, regelstand: str) -> StubSteuerung:
        return next(s for s in self.gebaut if s.regelstand == regelstand)
