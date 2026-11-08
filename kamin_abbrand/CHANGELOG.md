# Changelog

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
