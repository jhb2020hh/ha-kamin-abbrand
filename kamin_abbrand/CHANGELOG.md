# Changelog

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
