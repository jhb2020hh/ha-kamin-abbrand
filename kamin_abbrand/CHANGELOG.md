# Changelog

## 0.4.3

- Neu: Die Seite „Verlauf“ lädt sich alle 10 s selbst neu, das Zeitfenster läuft also mit. Solange der Zeiger auf der Grafik steht, bleibt sie stehen, damit der abgelesene Wert nicht wegspringt.

## 0.4.2

- Neu: Restlaufzeit bis zur Reinigung des Wärmetauschers. Die Startseite zeigt eine Kachel mit den verbleibenden Brennstunden, auch wenn die Reinigung noch nicht ansteht. In Home Assistant gibt es dazu `sensor.kamin_app_wt_reinigung_rest` in Stunden.
- Behoben: Eine Auflage wurde auch gezählt, wenn die Glut von selbst wieder aufflammte (Ausbrand → Anheizen → Abbrand) oder die Tür kurz nach dem Nachlegen zum Schüren geöffnet wurde. Mit Türkontakt zählt eine Auflage jetzt nur, wenn die Tür seit der letzten Auflage offen war, und frühestens 10 min nach ihr. Ohne Türkontakt bleibt es beim Übergang Anheizen → Abbrand. Die Holzmenge war dadurch um rund 10–25 % zu hoch.
- Behoben: Ein Tag galt als „gemessen“, auch wenn der Wärmemengenzähler nur einen Teil davon gesehen hat (spät zugewiesen, länger nicht lesbar). Die Kaminwärme war dann viel zu klein. Solche Tage werden jetzt aus dem Holz geschätzt.
- Geändert: Fehlt ein Zählerwert länger als eine Stunde und kommt am selben Tag wieder, wird der Zuwachs gebucht statt verworfen. Nur wenn Mitternacht in der Lücke liegt, beginnt der Zähler neu.
- Geändert: Die Prognose der Amortisation zieht die laufenden Kosten für ein ganzes Jahr ab, statt sie mit der Wärme hochzurechnen. Zehren sie den Jahresertrag auf, lautet das Urteil „keine Amortisation“.
- Hinweis: „Verhältnis Raum- zu Wasserwärme“ und „Anteil Aufstellraum“ bestimmen, wie viel Kaminwärme aus dem Wärmemengenzähler wird. Passen sie nicht zur gemessenen Einsparung, stimmt die Prognose nicht. Prüfe sie nach den ersten Wochen: Einsparung in kWh × Nutzungsgrad der Heizung ÷ Wasserwärme − 1 ergibt das Produkt beider Werte.

## 0.4.1

- Behoben: Im Betrieb konnte eine Automation in Home Assistant, die denselben Klappen-Helfer schreibt (z. B. die alte Phasensteuerung), die Klappe alle 30 s zurückstellen. Beim Einmessen pendelte die Klappe deshalb, statt auf 100 % zu bleiben.
- Neu: Die als „Konkurrierende Automation“ zugewiesene Automation schaltet die App jetzt selbst: beim Wechsel auf Betrieb aus, bei Lauschen und bei jedem Rückfall wieder an. Lässt sie sich nicht ausschalten, bleibt die App im Lauschen. Wird sie im Betrieb wieder eingeschaltet, schaltet die App sie einmal aus und meldet das.
- Neu: Schreibt trotzdem etwas anderes in den Klappen-Ausgang, stellt die App sofort ihren Wert wieder her und meldet das einmal je Betrieb.
- Hinweis unter Ein-/Ausgänge, wenn der Klappen-Ausgang ein Helfer ist und keine konkurrierende Automation zugewiesen ist.

## 0.4.0

- Neu: Einmessen unter Einstellungen → Einmessen. Du startest eine Aufzeichnung, zündest dann an und regelst von Hand an den Ofenklappen, die App hält die Zuluftklappe auf 100 %. Nach dem gewählten Nachlegen (1–5, erkannt an der Türöffnung oder ohne Türkontakt am wieder steigenden Abgas) übernimmt die Automatik, die Aufzeichnung läuft still weiter, bis der Ofen kalt ist. Danach schlägt die App im Trend Punkte vor (Anheizen Max, Abbrand stabil, Bester Abbrand, Nachlegen/Glut, Abkühlen, von Hand: Zu heiß). Du verschiebst sie und übernimmst die daraus abgeleiteten Grenzwerte einzeln. Aufzeichnungen bleiben gespeichert und sind löschbar, eine ist maßgeblich. Nur im Betrieb. Während der Handregelung drosselt die App bei Übertemperatur nicht, sie meldet nur. Sie endet auch bei Abbrechen, ohne Feuer nach 60 min, bei kaltem Ofen, nach 8 h, beim Wechsel auf Lauschen und nach einem Neustart der App.
- Neu: Nachrichtenart „Übertemperatur“ für Abgas und Wasser, im Lauschen wie im Betrieb. Die erste Warnung kommt nach 30 s (Abgas) bzw. 60 s (Wasser) über der Grenze, danach alle 5 min, bis der Wert wieder 10 K unter der Grenze ist. Dann kommt einmal „wieder normal“. Die beiden Automationen in Home Assistant, die das bisher meldeten, sind aus. Wichtig: Unter Benachrichtigungen ein Gerät zuordnen, sonst kommt nichts.
- Neu: Nachrichtenart „Einmessen“, gesendet nur, wenn du sie beim Start der Aufzeichnung wählst.
- Neu: Pumpe mit zwei Eingängen, „Pumpe Ein/Aus“ und „Pumpe Stellsignal“, sichtbar bei jedem wassergeführten Ofen. Ohne Ein/Aus gilt die Pumpe ab „Pumpe läuft ab“ (Anlage, Vorgabe 5 %) als laufend. Kachel auf der Startseite, Linie im Verlauf.
- Startseite: Umschalter Einfach/Experte. Gradient, I-Anteil und T-Maximum Phase stehen nur noch unter Experte.

## 0.3.9

- Neu: Fehler lassen sich im Betrieb von Hand quittieren, über die Karte „Fehler quittieren“ auf der Startseite oder den Knopf „Fehler quittieren“ der Integration. Ein Anheizfehler geht bei kaltem Ofen sofort in Standby, bei warmem Ofen in Ausbrand, Glut oder Abkühlen. Bisher musste man dafür 90 min warten. Eine Störung lässt sich quittieren, sobald der Fühler wieder frische Werte liefert. Eine Klappenstörung wird zurückgesetzt und meldet sich wieder, falls sie weiter besteht.

## 0.3.8

- Behoben: Eine Klappenrückmeldung in Volt wurde als Prozent gelesen (5,8 V ergaben „Iststellung 6 %“). Im Betrieb hätte das nach zwei Minuten „Klappe folgt nicht“ ausgelöst.
- Neu: Die Rückmeldung darf in %, V oder mA kommen. Unter Anlage lässt sich einstellen, welcher Wert „zu“ und welcher „auf“ bedeutet. Ohne Eintrag gelten 2–10 V bzw. 4–20 mA. Ein-/Ausgänge zeigt den umgerechneten Wert mit an.

## 0.3.7

- Startseite neu geordnet: Die Leistung (Eco/Normal/Power) steht direkt unter den Hinweisen, der Handbetrieb ganz unten.

## 0.3.6

- Neu: Handbetrieb für Klappentest und Wartung direkt in der App, als Karte auf der Startseite. Wählbar sind Klappenstellung (Schieber oder 0/50/100 %) und Dauer (15, 30, 60 oder 120 min). Danach übernimmt die Automatik von selbst. Die Begrenzung bei Übertemperatur bleibt aktiv.
- Der Handbetrieb wirkt nur im Betrieb. Im Lauschen stellt weiter die Automation die Klappe, die App schreibt dort nichts. Beim Wechsel auf Lauschen endet der Handbetrieb.
- Der Schalter „Handbetrieb“ der Integration schaltet jetzt im Betrieb den Handbetrieb der App, statt den Befehl abzulehnen.

## 0.3.5

- Behoben: Ein Kaltstart wurde als heißes Nachlegen gewertet, wenn das Feuer bei offener Tür schon hochgebrannt war, und die Klappe ging auf „Klappe beim Nachlegen“. Für den Startfall zählt jetzt die Temperatur von vor dem ersten Öffnen. Öffnet man die Tür im Anheizen erneut, bleibt der Startfall erhalten.
- Neu: Beim Kaltstart (keine Glut vor dem Anheizen) steht die Klappe auf „Klappe beim Kaltstart“ (100 %). Der Abbrand beginnt erst ab „Kaltstart offen bis“ (220 °C) oder nach der Höchstdauer Anheizen. Beide Werte sind unter Einstellungen → Anheizen einstellbar.

## 0.3.4

- Behoben: Lag die Seite länger im Hintergrund, zeigte sie „Fehler 401“, bis man von Hand neu lud. Die abgelaufene Anmeldung bei Home Assistant wird jetzt erkannt und die Seite einmal neu geladen. Im Hintergrund fragt die Seite nicht mehr ab.
- Behoben: Der Hinweis „Energiezähler ist nicht zugewiesen“ erschien auch, wenn beim Wärmeerzeuger ein Zähler eingetragen ist.

## 0.3.3

- Neu: Karte Abbrand-Dauer auf der Startseite mit aktuellem und letztem Abbrand, Durchschnitt und Anzahl für heute und gesamt. Vorhandene Summen der Live-Automation werden einmalig übernommen.
- Neu: Gradient, T-Maximum der Phase, Phasenbeginn, Lüftungsfreigabe (neue Rolle „Lüftung frei“) und Warnung „Klappe folgt nicht“ auf der Startseite.
- Neu: Der Verlauf zeigt zusätzlich Wärmeleistung, Ziel, Klappe Ist und Wärmetauscher. Jede Linie lässt sich ein- und ausschalten.
- Die Rolle „Klappe Iststellung“ akzeptiert jetzt auch Sensoren.

## 0.3.2

- Behoben: Die Startseite zeigte nach der Wahl der Leistung weiter das Ziel der Automation statt des Ziels der App.

## 0.3.1

- Neu: Knöpfe Eco, Normal und Power auf der Startseite, Zieltemperatur je Stufe unter Einstellungen → Parameter. Die Stufe gilt für die App, die Live-Automation in Home Assistant bleibt unberührt.
- Neu: Hinweis auf der Startseite mit Direktlinks zu Anlage und Ein-/Ausgänge, solange Angaben fehlen.
- Behoben: Nach einem Update lief im Browser teils noch das alte JavaScript, dadurch fehlten die Reiter unter Einstellungen.

## 0.3.0

- Neu: Umschalter Lauschen/Betrieb. Im Betrieb steuert die App die Zuluftklappe. Das geht nur nach Prüfung aller Bedingungen und einmaliger Bestätigung. Sicherer Zustand ist Klappe 100 % offen.
- Neu: Anlagenprofil für jedes System: Ofen normal oder wassergeführt, beliebige Kombination von Wärmeerzeugern, Einheiten, Währung.
- Neu: Seite Kosten mit Brennstoffvergleich und Amortisation, gemessen oder als gekennzeichnete Schätzung.
- Neu: Wasserseite mit Warnungen, Wärmemengenzähler für Energie und Leistung. Die App schaltet dort nichts.
- Neu: Push-Nachrichten, je Nachrichtenart wählbar für jedes Gerät. Vorgabe: keine.
- Neu: Erinnerung zum Reinigen des Wärmetauschers nach einstellbaren Brennstunden.
- Geändert: Parameter, Anlage, Ein-/Ausgänge, Regler und Benachrichtigungen liegen unter Einstellungen.
- Entfernt: Option `modus`. Die Rolle `ausgang_meldungen` ist durch die Geräteauswahl ersetzt.

## 0.2.2

- Behoben: Die Iststellung der Klappe wird auch von Cover- und Licht-Entitäten gelesen.

## 0.2.1

- Behoben: Die App-Optionen ließen sich nicht speichern (fehlende Option `entitaeten`). Der alte Block ist entfernt, zugewiesen wird nur noch in der Oberfläche.

## 0.2.0

- Eigene Oberfläche in der Seitenleiste: Start, Verlauf, Ein-/Ausgänge, Einstellungen, Regler, Service.
- Alle Ein- und Ausgänge sind zuweisbar, gespeichert in `/config/einstellungen.json`. Wechsel ohne Neustart.
- Türkontakt wählbar. Ohne Helfer der Live-Automation startet die App im Standby und rechnet Anstieg und Maximum selbst.
- Eigene Reglerwerte für den Regelstand `neu`, jede Änderung wird protokolliert.
- Auto-Tune aus dem Verlauf. Der aktive Testlauf ist bis zur Umschaltung gesperrt.
- Mitgelieferte Integration „Kamin Abbrand“ für Home Assistant.
- Keine festen Sensoren und kein fester Hostname mehr, damit die App auch über eine Repository-URL läuft.

## 0.1.0

- Erste Fassung für den Parallelbetrieb: lesen, rechnen, protokollieren. Die App steuert nichts.
