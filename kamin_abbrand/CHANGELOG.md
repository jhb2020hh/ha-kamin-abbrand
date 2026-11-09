# Changelog

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
