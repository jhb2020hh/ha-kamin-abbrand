# Kamin Abbrand

Home-Assistant-App für Kaminöfen. Sie erkennt die Phasen (Anheizen, Abbrand, Glut, Abkühlen), rechnet aus, wie weit die Zuluftklappe offen sein sollte, und kann sie auf Wunsch steuern. Dazu kommen ein Kostenvergleich mit Amortisation, die Überwachung der Wasserseite und Push-Nachrichten je Gerät.

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjhb2020hh%2Fha-kamin-abbrand)

![Startseite: Phase Abbrand mit Abgastemperatur, Klappenstellung, Kaminzug und Wasserseite](docs/screenshots/start.png)

## Einblicke

| Verlauf | Kosten |
| --- | --- |
| [![Verlauf von Abgastemperatur, Klappe und Kaminzug über drei Auflagen](docs/screenshots/verlauf.png)](docs/screenshots/verlauf.png) | [![Amortisation und Brennstoffvergleich](docs/screenshots/kosten.png)](docs/screenshots/kosten.png) |
| Abgastemperatur, Klappe und Kaminzug der letzten Stunden, darunter die Phasen als Farbband. | Amortisation des Ofens und der Preis je kWh Nutzwärme im Vergleich zu deinen anderen Wärmeerzeugern. |

| Einstellungen |
| --- |
| [![Zuweisung der Sensoren aus Home Assistant](docs/screenshots/einstellungen.png)](docs/screenshots/einstellungen.png) |
| Jeder Ein- und Ausgang lässt sich einem Sensor aus Home Assistant zuweisen, ohne Neustart und ohne YAML. |

Die Bilder zeigen eine Beispielanlage mit erfundenen Messwerten.

## Lauschen und Betrieb

**Die App startet im Modus Lauschen und schaltet dann nichts.** Sie liest Messwerte, rechnet mit und schreibt eigene Sensoren `sensor.kamin_app_*`. Nachrichten sendet sie nur an Geräte, die du ausgewählt hast.

Im Modus **Betrieb** steuert die App die Zuluftklappe. Einschalten lässt er sich erst, wenn alle Bedingungen erfüllt sind und du die Sicherheitshinweise bestätigt hast.

**Warnung: Eine falsch gestellte Zuluftklappe kann das Feuer ersticken und zu Kohlenmonoxid im Raum führen.** Die App ist experimentell und bisher an einem einzigen wassergeführten Ofen entwickelt, die voreingestellten Reglerwerte passen zu anderen Öfen möglicherweise nicht. Kläre den Betrieb mit Hersteller und Schornsteinfeger, betreibe einen CO-Melder und fahre den ersten Abbrand unter Aufsicht. Die Pumpe eines wassergeführten Ofens schaltet die App nie.

## Voraussetzungen

- Home Assistant OS oder Supervised (die App läuft als Container unter dem Supervisor)
- ein Sensor in Home Assistant, der die Abgastemperatur des Ofens in °C liefert
- optional: Türkontakt, Wasserfühler, Pufferspeicher, Kaminzug, Wärmemengenzähler, Zähler der bisherigen Heizung
- für den Betrieb: ein ansteuerbarer Klappenantrieb, möglichst mit Watchdog

## Installation

1. Oben auf den Button klicken, oder unter Einstellungen → Apps → App installieren im Menü **Repositories** diese URL eintragen:
   `https://github.com/jhb2020hh/ha-kamin-abbrand`
2. „Kamin Abbrand“ installieren. Das Abbild wird auf dem Gerät gebaut, das dauert einige Minuten.
3. App starten, in der Seitenleiste „Kamin“ öffnen und unter **Einstellungen → Ein-/Ausgänge** die Abgastemperatur zuweisen.

Die vollständige Anleitung steht in der App auf dem Reiter „Dokumentation“.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install pytest aiohttp
.venv/bin/python -m pytest tests/kern tests/anbindung
```

## Hinweis

Die Software wird ohne Gewähr bereitgestellt. Sie ersetzt keine Sicherheitseinrichtung des Ofens, keinen CO-Melder und nicht die Vorgaben von Hersteller und Schornsteinfeger.

## Unterstützen

Kamin Abbrand entsteht in der Freizeit, an einem einzigen Ofen, und bleibt kostenlos und quelloffen. Wenn dir die App einen Abend am Feuer angenehmer macht oder beim Rechnen hilft, freue ich mich über einen Kaffee:

<p align="center">
  <a href="https://buymeacoffee.com/jhb2020hh">
    <img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-einen%20Kaffee%20ausgeben-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Einen Kaffee ausgeben auf Buy Me a Coffee" height="44">
  </a>
</p>

Genauso wertvoll und ganz ohne Geld:

| | |
| --- | --- |
| ⭐ **Stern vergeben** | Ein Stern oben rechts hilft anderen Ofenbesitzern, das Projekt zu finden. |
| 🔥 **Vom eigenen Ofen berichten** | Die Reglerwerte stammen von einem einzigen Ofen. Schreib in einem [Issue](https://github.com/jhb2020hh/ha-kamin-abbrand/issues), welcher Ofen bei dir läuft und wie die App im Modus Lauschen rechnet. |
| 🐞 **Fehler melden** | Passt eine Phase nicht oder fehlt dir etwas, dann [öffne ein Issue](https://github.com/jhb2020hh/ha-kamin-abbrand/issues/new), am besten mit einem Bild vom Verlauf. |

## Lizenz

Apache License 2.0, siehe [LICENSE](LICENSE).
