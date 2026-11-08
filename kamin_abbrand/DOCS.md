# Kamin Abbrand

Die App rechnet die Abbrandsteuerung des Kaminofens als eigenes Programm neben Home Assistant.

## Was sie macht

Die App läuft im Beobachtungsbetrieb. Eine vorhandene Automation bleibt die Steuerung.

- Die Oberfläche öffnest du in der Seitenleiste unter „Kamin“.

- Die App liest die Messwerte und Helfer aus Home Assistant.
- Sie rechnet zwei Regelstände: `paritaet` wie die Live-Automation und `neu` mit den offenen Themen. `neu` läuft erst, wenn der Kern ihn anbietet.
- Sie zeigt das Ergebnis als Schattensensoren `sensor.kamin_app_*` an.
- Sie schreibt jeden Lauf in `/config/verlauf/verlauf-JJJJ-MM-TT.jsonl` (auf dem Host unter `/addon_configs/`).

**Die App steuert nichts.** Sie ruft keine Dienste auf, setzt keine Helfer, bewegt die Klappe nicht und verschickt keine Nachrichten. Die Schreibschicht kann technisch nur Zustände mit dem Präfix `sensor.kamin_app_` setzen. Aktionen und Meldungen des Kerns landen nur im Protokoll.

## Voraussetzungen

- Home Assistant mit Supervisor, also Home Assistant OS oder Supervised.
- Optional die Helfer einer Live-Automation mit dem eingestellten Präfix, zum Beispiel `input_select.kamin_abbrand_phase`.
- Ein Sensor für die Abgastemperatur.

Die App braucht den Zugriff auf die Home-Assistant-API (`homeassistant_api`). Dieser Zugang hat Administratorrechte. Die Begrenzung auf die Schattensensoren liegt deshalb im Code der App.

## Installation

1. Einstellungen → Apps → App installieren öffnen.
2. Im Menü oben rechts **Repositories** wählen und die URL dieses Repositorys eintragen.
3. „Kamin Abbrand“ installieren. Das Abbild wird auf dem Gerät gebaut, das dauert einige Minuten.
4. App starten und in der Seitenleiste unter „Kamin“ die Abgastemperatur zuweisen.

## Einrichten

Auf der Seite **Ein-/Ausgänge** weist du Sensoren und Schalter zu. Pflicht ist nur die Abgastemperatur. Der Wechsel gilt ohne Neustart. Gibt es keinen Sensor für Anstieg, Maximum oder T.WT-Anstieg, rechnet die App sie selbst.

Fehlen die Helfer einer Live-Automation, startet die App nach einer Minute im Standby mit ihren Vorgaben.

Unter Einstellungen → Apps bleiben nur `log_stufe` und `helfer_praefix`. Der Block `entitaeten` dient in Version 0.2.0 nur noch dem Übernehmen der alten Zuweisung.

## Regler und Auto-Tune

Auf der Seite **Regler** änderst du Kp, Tn und Kd ohne Neubau. Bis zur Umschaltung wirkt das nur auf den Regelstand `neu`, nicht auf den Ofen. „Aus dem Verlauf berechnen“ wertet die Klappenbewegungen der letzten sieben Tage nach CHR aperiodisch aus und liefert einen Vorschlag, den du bestätigst. Der Testlauf mit pendelnder Klappe ist bis zur Umschaltung gesperrt.

## Integration

Die App kopiert beim Start die Integration nach `custom_components/kamin_abbrand` und ändert in der HA-Konfiguration sonst nichts. Nach einem Neustart von Home Assistant fügst du sie unter Geräte & Dienste hinzu. Adresse und Token stehen auf der Seite **Service**. Bedienelemente, die den Ofen beträfen, lehnen im Beobachtungsbetrieb jeden Befehl mit einer Meldung ab.

## Schattensensoren

| Sensor | Inhalt |
|---|---|
| `sensor.kamin_app_p_phase`, `sensor.kamin_app_n_phase` | Phase |
| `sensor.kamin_app_p_klappe_soll`, `sensor.kamin_app_n_klappe_soll` | Klappe Soll in % |
| `sensor.kamin_app_p_i_anteil`, `sensor.kamin_app_n_i_anteil` | I-Anteil des Reglers |
| `sensor.kamin_app_p_signal`, `sensor.kamin_app_n_signal` | Signalzustand des Abgaswerts |
| `sensor.kamin_app_status` | `start`, `wartet`, `ok`, `fehler` oder `gestoppt` |

`p` steht für `paritaet`, `n` für `neu`. Die Sensoren werden bei Änderung und sonst mindestens alle 60 Sekunden geschrieben. Es sind einfache Zustände ohne Entitätsregistrierung. Nach einem Neustart von Home Assistant fehlen sie, bis die App sie wieder schreibt.

## Verlauf

Je Tag entsteht eine Datei mit einer JSON-Zeile je Lauf (Eingaben, Ergebnis und der Zustand der Live-Automation). Dateien älter als 30 Tage löscht die App. Die Dateien sind von Sicherungen ausgenommen.

## Grenzen

- Ohne Verbindung zu Home Assistant rechnet die App nicht. Sie verbindet sich neu, die Wartezeit steigt bis 60 Sekunden.
- Die Zustände der Regelstände übernimmt sie nur beim ersten Start aus den Helfern. Danach rechnet sie eigenständig weiter, auch wenn die Live-Automation abweicht. Genau diese Abweichung soll der Parallelbetrieb zeigen.
