# Kamin Abbrand

Home-Assistant-App, die den Abbrand eines Kaminofens beobachtet. Sie erkennt die Phasen (Anheizen, Abbrand, Glut, Abkühlen) und rechnet aus, wie weit die Zuluftklappe offen sein sollte.

[![Repository zu Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjhb2020hh%2Fha-kamin-abbrand)

## Stand: Beobachtungsbetrieb

**Diese Version steuert nichts.** Sie bewegt keine Klappe, schaltet keine Geräte und verschickt keine Nachrichten. Sie liest Messwerte und schreibt nur eigene Sensoren `sensor.kamin_app_*`.

Die App ist experimentell. Die voreingestellten Reglerwerte stammen von einem einzigen wassergeführten Ofen und passen zu anderen Öfen möglicherweise nicht. Eine Klappensteuerung für andere Anlagen ist derzeit nicht freigegeben.

## Voraussetzungen

- Home Assistant OS oder Supervised (die App läuft als Container unter dem Supervisor)
- ein Sensor in Home Assistant, der die Abgastemperatur des Ofens in °C liefert
- optional: Türkontakt, Wassertemperatur, Pufferspeicher, Kaminzug

## Installation

1. Oben auf den Button klicken, oder unter Einstellungen → Apps → App installieren im Menü **Repositories** diese URL eintragen:
   `https://github.com/jhb2020hh/ha-kamin-abbrand`
2. „Kamin Abbrand“ installieren. Das Abbild wird auf dem Gerät gebaut, das dauert einige Minuten.
3. App starten, in der Seitenleiste „Kamin“ öffnen und unter **Ein-/Ausgänge** die Abgastemperatur zuweisen.

Die vollständige Anleitung steht in der App auf dem Reiter „Dokumentation“.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install pytest aiohttp
.venv/bin/python -m pytest tests/kern tests/anbindung
```

## Hinweis

Die Software wird ohne Gewähr bereitgestellt. Sie ersetzt keine Sicherheitseinrichtung des Ofens, keinen CO-Melder und nicht die Vorgaben von Hersteller und Schornsteinfeger.

## Lizenz

Apache License 2.0, siehe [LICENSE](LICENSE).
