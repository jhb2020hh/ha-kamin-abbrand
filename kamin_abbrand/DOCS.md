# Kamin Abbrand

Die App überwacht den Abbrand eines Kaminofens und kann auf Wunsch die Zuluftklappe steuern. Sie vergleicht außerdem die Heizkosten mit deiner übrigen Heizung und überwacht bei wassergeführten Öfen die Wasserseite.

## Sicherheit

**Eine falsch gestellte Zuluftklappe kann das Feuer ersticken und zu Kohlenmonoxid im Raum führen.** Die App ersetzt keine Sicherheitseinrichtung des Ofens, keinen CO-Melder, keine thermische Ablaufsicherung und nicht die Vorgaben von Hersteller und Schornsteinfeger. Die voreingestellten Reglerwerte stammen von einem einzigen Ofen. Du nutzt die App auf eigene Verantwortung.

## Lauschen und Betrieb

Die App startet immer im Modus **Lauschen**. Der Knopf auf der Startseite schaltet um.

| | Lauschen | Betrieb |
|---|---|---|
| Rechnen und anzeigen | ja | ja |
| Push-Nachrichten | nach deiner Auswahl | nach deiner Auswahl |
| Klappe, Watchdog, Fühler-Neustart | nie | ja |
| Pumpe | nie | nie |

In den Betrieb schaltet die App erst, wenn alle Bedingungen erfüllt sind. Was noch fehlt, zeigt sie dir an:

- Die Klappe ist unter Anlage als motorisiert eingetragen, „Klappe Soll“ ist zugewiesen und erreichbar.
- Der Abgasfühler liefert einen frischen, plausiblen Wert, Home Assistant ist verbunden, und der Regelstand `neu` liefert Ergebnisse.
- Eine als „Konkurrierende Automation“ zugewiesene Automation ist aus. **Die App schaltet sie nicht ab.** Zwei Regler auf derselben Klappe arbeiten gegeneinander. Hast du mehrere Automationen, die die Klappe bewegen, schalte alle selbst ab; überwacht wird nur die zugewiesene.
- Ein Watchdog ist zugewiesen, oder du bestätigst, dass die Klappe bei einem Ausfall nicht schließen kann.
- Du bestätigst einmalig: CO-Melder vorhanden, Betrieb mit Hersteller oder Schornsteinfeger geklärt, Nutzung auf eigene Verantwortung.

Im Betrieb führt der Regelstand `neu`. Rollen und Anlage lassen sich dann nicht ändern. Unter Anlage legst du fest, ob der Ausgang invertiert ist und welche Startspannung ein Antrieb mit 2-10-V-Eingang braucht.

**Sicherer Zustand ist Klappe 100 % offen.** Dorthin fährt die App beim Zurückschalten auf Lauschen, beim Beenden, bei einem Fehler im Rechenkern und wenn eine Bedingung im Lauf wegfällt. In den letzten beiden Fällen fällt sie außerdem auf Lauschen zurück und meldet das. Kurze Ausfälle von Fühler, Klappe oder Verbindung überbrückt sie 30 Sekunden. Solange die App ein heißes Feuer nicht einordnen kann, bleibt die Klappe offen. Ist die Verbindung zu Home Assistant weg, kann die App nichts mehr schalten; dafür ist der Watchdog da. Fahre den ersten Abbrand im Betrieb unter Aufsicht.

## Voraussetzungen

- Home Assistant mit Supervisor, also Home Assistant OS oder Supervised.
- Ein Sensor für die Abgastemperatur. Alles Weitere ist optional, und die App sagt dir, welche Funktion was braucht.

Die App braucht den Zugriff auf die Home-Assistant-API (`homeassistant_api`). Dieser Zugang hat Administratorrechte. Die Begrenzung liegt deshalb im Code der App: Sie schreibt nur eigene Sensoren `sensor.kamin_app_*`, ruft `notify.*` für deine Nachrichten auf und schaltet ausschließlich im Betrieb die zugewiesenen Ausgänge.

## Installation

1. Einstellungen → Apps → App installieren öffnen.
2. Im Menü oben rechts **Repositories** wählen und die URL dieses Repositorys eintragen.
3. „Kamin Abbrand“ installieren. Das Abbild wird auf dem Gerät gebaut, das dauert einige Minuten.
4. App starten und in der Seitenleiste „Kamin“ öffnen.

## Einstellungen

Das Menü **Einstellungen** hat fünf Reiter:

- **Parameter:** Grenzwerte und Vorgaben. Es erscheint nur, was zu deiner Anlage passt.
- **Anlage:** Ofen normal (luftgeführt) oder wassergeführt, Puffer, Pumpe, Klappe, Türkontakt, Wärmeerzeuger, Einheiten, Holz. Die Erzeuger lassen sich beliebig kombinieren.
- **Ein-/Ausgänge:** Sensoren und Schalter zuweisen. Pflicht ist nur die Abgastemperatur. Änderungen gelten ohne Neustart. Bei wassergeführten Öfen weist du hier auch die Fühler und den Wärmemengenzähler zu, getrennt nach Energie und Leistung.
- **Regler:** Kp, Tn und Kd sowie Auto-Tune. „Aus dem Verlauf berechnen“ wertet die Klappenbewegungen der letzten sieben Tage nach CHR aperiodisch aus und liefert einen Vorschlag, den du bestätigst. Der Testlauf mit gezielter Klappenbewegung ist derzeit gesperrt.
- **Benachrichtigungen:** Für jede Nachrichtenart wählst du die Geräte. Ohne Haken wird nichts gesendet. Je Gerät gibt es eine Testnachricht.

Unter Einstellungen → Apps stehen nur `log_stufe` und `helfer_praefix`. Fehlen die Helfer einer Live-Automation, startet die App nach einer Minute im Standby mit ihren Vorgaben.

## Kosten

Die Seite **Kosten** vergleicht Holz mit deinen Wärmeerzeugern je kWh Nutzwärme und rechnet die Amortisation. Sie ist eine Rechenhilfe.

- Preise sind nicht vorbelegt. Was keinen Preis hat, erscheint nicht im Vergleich, und die Seite nennt, was fehlt.
- Mit Wärmemengenzähler und Zähler der bisherigen Heizung ist die Einsparung **gemessen**, sonst eine gekennzeichnete **Schätzung** aus dem Holz. Eine gemessene Einsparung entsteht erst am abgeschlossenen Tag.
- Eine Auflage zählt die App beim Übergang von Anheizen zu Abbrand. Ohne „kg je Auflage“ zählt sie nur und bucht kein Holz. Korrigieren kannst du von Hand.
- Die Referenz (Verbrauch über Außentemperatur) kannst du eintragen oder nach mindestens 14 Tagen ohne Feuer vorschlagen lassen.
- Ein Urteil zur Amortisation gibt es erst ab 50 kWh Kaminwärme. Der Tagespreis wird fest gebucht, spätere Preisänderungen ändern die Vergangenheit nicht.

## Wasserseite

Nur bei wassergeführten Öfen. Die App warnt bei zu kaltem Rücklauf, zu hohem Vorlauf, zu heißem Wärmetauscher, stehender Pumpe bei heißem Wasser und vollem Puffer. Jede Prüfung läuft nur, wenn ihr Fühler zugewiesen ist, die Grenzen stellst du unter Parameter ein. **Die App schaltet auf der Wasserseite nichts und ersetzt keine thermische Ablaufsicherung.**

Unter Parameter → Wartung stellst du ein, nach wie vielen Brennstunden der Wärmetauscher gereinigt werden soll (Vorgabe 100, 0 = aus, maßgeblich ist die Herstellerangabe). Die Erinnerung erscheint auf der Startseite und kommt als Nachricht, bis du „Gereinigt“ drückst.

## Integration

Die App kopiert beim Start die Integration nach `custom_components/kamin_abbrand` und ändert in der HA-Konfiguration sonst nichts. Nach einem Neustart von Home Assistant fügst du sie unter Geräte & Dienste hinzu. Adresse und Token stehen auf der Seite **Service**. Im Lauschen lehnen Bedienelemente, die den Ofen beträfen, jeden Befehl mit einer Meldung ab.

## Schattensensoren

| Sensor | Inhalt |
|---|---|
| `sensor.kamin_app_p_phase`, `sensor.kamin_app_n_phase` | Phase |
| `sensor.kamin_app_p_klappe_soll`, `sensor.kamin_app_n_klappe_soll` | Klappe Soll in % |
| `sensor.kamin_app_p_i_anteil`, `sensor.kamin_app_n_i_anteil` | I-Anteil des Reglers |
| `sensor.kamin_app_p_signal`, `sensor.kamin_app_n_signal` | Signalzustand des Abgaswerts |
| `sensor.kamin_app_status` | `start`, `wartet`, `ok`, `fehler` oder `gestoppt` |
| `sensor.kamin_app_wt_reinigung` | `ok`, `faellig` oder `aus` |
| `sensor.kamin_app_wasser_status` | `ok`, `hinweis`, `warnung` oder `alarm`, Texte als Attribut |
| `sensor.kamin_app_amortisation` | Amortisation |

`p` steht für `paritaet`, `n` für `neu`. Die Sensoren werden bei Änderung und sonst mindestens alle 60 Sekunden geschrieben. Es sind einfache Zustände ohne Entitätsregistrierung. Nach einem Neustart von Home Assistant fehlen sie, bis die App sie wieder schreibt.

## Verlauf

Je Tag entsteht `/config/verlauf/verlauf-JJJJ-MM-TT.jsonl` (auf dem Host unter `/addon_configs/`) mit einer JSON-Zeile je Lauf. Dateien älter als 30 Tage löscht die App. Die Dateien sind von Sicherungen ausgenommen.

## Grenzen

- Ohne Verbindung zu Home Assistant rechnet und schaltet die App nicht. Sie verbindet sich neu, die Wartezeit steigt bis 60 Sekunden.
- Im Lauschen übernimmt sie die Zustände nur beim ersten Start aus den Helfern einer Live-Automation und rechnet danach eigenständig weiter.
- Die Oberfläche ist derzeit nur auf Deutsch.
