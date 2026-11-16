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
- Eine als „Konkurrierende Automation“ zugewiesene Automation schaltet die App beim Wechsel auf Betrieb selbst aus und bei Lauschen oder einem Rückfall wieder an. Lässt sie sich nicht ausschalten, bleibt die App im Lauschen. Zwei Regler auf derselben Klappe arbeiten gegeneinander: Gibt es weitere Automationen, die die Klappe bewegen, schalte sie selbst ab. Schreibt im Betrieb trotzdem etwas in den Klappen-Ausgang, stellt die App sofort ihren Wert wieder her und meldet das.
- Ein Watchdog ist zugewiesen, oder du bestätigst, dass die Klappe bei einem Ausfall nicht schließen kann.
- Du bestätigst einmalig: CO-Melder vorhanden, Betrieb mit Hersteller oder Schornsteinfeger geklärt, Nutzung auf eigene Verantwortung.

Im Betrieb führt der Regelstand `neu`. Rollen und Anlage lassen sich dann nicht ändern. Unter Anlage legst du fest, ob der Ausgang invertiert ist und welche Startspannung ein Antrieb mit 2-10-V-Eingang braucht.

**Sicherer Zustand ist Klappe 100 % offen.** Dorthin fährt die App beim Zurückschalten auf Lauschen, beim Beenden, bei einem Fehler im Rechenkern und wenn eine Bedingung im Lauf wegfällt. In den letzten beiden Fällen fällt sie außerdem auf Lauschen zurück und meldet das. Kurze Ausfälle von Fühler, Klappe oder Verbindung überbrückt sie 30 Sekunden. Solange die App ein heißes Feuer nicht einordnen kann, bleibt die Klappe offen. Ist die Verbindung zu Home Assistant weg, kann die App nichts mehr schalten; dafür ist der Watchdog da. Fahre den ersten Abbrand im Betrieb unter Aufsicht.

### Rückmeldung der Klappe

Die Rolle „Klappe Iststellung“ darf einen Sensor in %, V oder mA bekommen. Die App rechnet linear in Prozent um. Ohne eigene Werte gilt die Vorgabe der Einheit: 0–100 % bzw. 2–10 V bzw. 4–20 mA. Misst dein Antrieb andere Endwerte, trägst du unter Anlage „Rückmeldung bei zu“ und „bei auf“ ein, zum Beispiel 1,87 und 10,01 V. Ist der Wert bei zu größer als bei auf, gilt die Rückmeldung als invertiert. Bei einer anderen Einheit (etwa mV) braucht die App beide Werte. Bis dahin warnt die Seite Ein-/Ausgänge, und die Iststellung bleibt leer.

### Handbetrieb

Zum Testen der Klappe und für die Wartung gibt es auf der Startseite die Karte **Handbetrieb**. Dort stellst du die Klappe mit dem Schieber oder mit 0, 50 und 100 % und wählst eine Dauer von 15, 30, 60 oder 120 Minuten. Ist die Zeit um, übernimmt wieder die Automatik und die App meldet das. „Verlängern“ startet die Zeit neu.

Der Handbetrieb wirkt nur im **Betrieb**. Im Lauschen stellt deine Automation die Klappe, nutze dort deren Handbetrieb. Beim Wechsel auf Lauschen und bei einem Rückfall endet der Handbetrieb. Die Begrenzung bei Übertemperatur bleibt auch im Handbetrieb aktiv. Der Schalter „Handbetrieb“ der Integration schaltet denselben Handbetrieb.

### Fehler quittieren

Es gibt drei Arten von Fehlern. Bei allen steht die Klappe auf 100 %.

| Fehler | Ursache | Ende ohne Zutun |
|---|---|---|
| **Störung** | Der Abgasfühler (oder der LUC) liefert keine frischen Werte, siehe Parameter → Sicherheit | sobald wieder frische Werte kommen |
| **Anheizfehler** | Abbrandtemperatur nicht erreicht oder Feuer nach < 15 min zusammengefallen | nach 90 min Standby, bei Temperaturanstieg wieder Anheizen |
| **Klappenstörung** | Klappe nimmt keine Befehle an oder folgt der Sollstellung nicht | sobald ein Befehl wieder ankommt bzw. die Stellung wieder passt |

Im **Betrieb** erscheint bei einem Fehler auf der Startseite die Karte **Fehler quittieren**, in Home Assistant gibt es dafür den Knopf „Fehler quittieren“. Behebe zuerst die Ursache und quittiere dann. Ist der Ofen kalt, geht die Steuerung zurück in **Standby**. Ist er noch warm, wechselt sie in die passende Phase, also Ausbrand, Glut oder Abkühlen, nach einer Störung in Anheizen. Eine Störung lässt sich erst quittieren, wenn der Fühler wieder frische Werte liefert. Bis dahin bleibt die Klappe offen. Besteht ein Klappenfehler weiter, meldet er sich nach dem Quittieren wieder.

Im **Lauschen** führt die Automation in Home Assistant die Phase, die App quittiert dort nichts. Ist die App wegen eines Fehlers auf Lauschen zurückgefallen, schaltest du nach der Behebung den Betrieb wieder ein.

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

Das Menü **Einstellungen** hat sechs Reiter:

- **Parameter:** Grenzwerte und Vorgaben. Es erscheint nur, was zu deiner Anlage passt.

  Die Mindestklappe im Abbrand („Klappe mindestens“, beim Entgasen und bei Briketts) geht bis 0 %. Unter 5 % fragt die App nach, weil der Regler die Luft dann auch bei voller Flamme ganz schließen darf. Das Feuer kann schwelen und viel Kohlenmonoxid bilden. Stelle das nur ein, wenn ein CO-Melder im Raum ist. Die Bestätigung protokolliert die App mit Zeit und Wert im Änderungsprotokoll (`aenderungen.jsonl`, Eintrag `rueckfrage_bestaetigt`) und zeigt sie am Feld an. Ein Wert ab 5 % oder „Zurücksetzen“ hebt sie auf.
- **Anlage:** Ofen normal (luftgeführt) oder wassergeführt, Puffer, Pumpe, Klappe, Türkontakt, Wärmeerzeuger, Einheiten, Holz. Die Erzeuger lassen sich beliebig kombinieren.
- **Ein-/Ausgänge:** Sensoren und Schalter zuweisen. Pflicht ist nur die Abgastemperatur. Änderungen gelten ohne Neustart. Bei wassergeführten Öfen weist du hier auch die Fühler und den Wärmemengenzähler zu, getrennt nach Energie und Leistung.
- **Regler:** Oben die Wahl der Regelstrategie: „Klassisch“ (PI mit Vorhalt, Vorgabe) oder „Regelstrategie 2.0“ (PI ohne Vorhalt mit Schwingungswächter). Darunter stehen die Werte der gewählten Strategie. Bei 2.0 zeigt die Seite die Dämpfung, die wirksamen Werte und den Zeitpunkt der letzten Dämpfung, dazu „Dämpfung auf 100 % zurücksetzen“. Was die Werte bewirken, steht unten unter „Regelung und Parameter erklärt“.

  Bei „Klassisch“ gibt es Kp, Tn und Kd sowie Auto-Tune. „Aus dem Verlauf berechnen“ prüft den Abbrand im Betrieb seit der letzten Regleränderung (höchstens sieben Tage, mindestens 30 min Abbrand) auf Schwingungen der Abgastemperatur. Schwingt sie, senkt der Vorschlag die Verstärkung, die der Regler bei dieser Periode wirklich hat: bei abklingender Schwingung auf 70 %, bei Dauerschwingung auf ein Drittel. Tn wird mindestens 2,2 Perioden, und der D-Anteil geht auf 0. Ist der Regelkreis ruhig, gibt es keinen neuen Vorschlag, die Werte werden also nie schärfer. „Vorschlag übernehmen“ setzt Kp, Tn und Kd zusammen. Der Testlauf mit gezielter Klappenbewegung ist derzeit gesperrt.

  Der D-Anteil (Kd) wirkt auf den Temperaturanstieg der letzten 2 min. Der hinkt etwa eine Minute nach. Bei Schwingungen um 3–4 min wirkt Kd deshalb wie zusätzliche Verstärkung: Kd 1 verdreifacht sie dort ungefähr. Lass ihn auf 0.
- **Einmessen:** Aufzeichnung eines von Hand geregelten Abbrands, aus der die App Grenzwerte für deinen Ofen und Fühler vorschlägt (siehe unten).
- **Benachrichtigungen:** Für jede Nachrichtenart wählst du die Geräte. Ohne Haken wird nichts gesendet. Je Gerät gibt es eine Testnachricht. „Übertemperatur“ meldet Abgas oder Wasser über der Grenze, im Lauschen wie im Betrieb, alle 5 min erneut und mit Entwarnung 10 K unter der Grenze.

### Einmessen

Jeder Ofen und jeder Fühler ist anders. Beim Einmessen zeichnet die App einen Abbrand auf, den du selbst regelst, und leitet daraus die Grenzwerte ab.

1. Im **Betrieb** und bei kaltem Ofen unter Einstellungen → Einmessen die Anzahl der Nachlegezyklen (1–5) wählen, auf Wunsch Push und eine Notiz, dann starten.
2. Anzünden. Die Zuluftklappe steht auf 100 %, als wäre sie nicht da. Du regelst an den Luftschiebern des Ofens wie gewohnt. Übertemperatur wird nur gemeldet, nicht gedrosselt.
3. Nach dem letzten Nachlegen (Türöffnung, ohne Türkontakt am wieder steigenden Abgas) übernimmt die Automatik. Die Aufzeichnung läuft still weiter, bis der Ofen kalt ist, damit das Abkühlen sichtbar wird.

Sie endet sofort bei „Abbrechen“, ohne Feuer nach 60 min (auch wenn das Feuer gleich wieder ausgeht), bei kaltem Ofen, nach 8 h, beim Wechsel auf Lauschen, nach einem Neustart der App und wenn das Aufzeichnen dreimal in Folge fehlschlägt. Die Automatik übernimmt dann sofort. Das letzte Nachlegen gilt erst, wenn die Tür 30 s zu ist. Meldet der Türkontakt länger als 2 min nichts, erkennt die App das Nachlegen am Temperaturverlauf. Danach setzt die App im Trend Punkte: Anheizen Max (→ Kaltstart offen bis), Abbrand stabil (→ Abbrandstart und Anstieg für die Feuererkennung), Bester Abbrand (→ Zieltemperaturen und Ausbrand-Abfall), Nachlegen/Glut (→ Glut), Abkühlen (→ Abkühlen). „Zu heiß“ (→ Übertemperatur) setzt du nur selbst. Ziehe die Punkte an die richtige Stelle, speichere sie und übernimm die Vorschläge einzeln. Bei mehreren Zyklen gilt das Mittel. Alle Aufzeichnungen bleiben gespeichert, eine ist maßgeblich.

Die Leistung wählst du mit Eco, Normal und Power auf der Startseite. Sie setzt das Ziel der App, die Zieltemperaturen je Stufe stehen unter Parameter. Eine Live-Automation in Home Assistant bleibt davon unberührt. Im Lauschen wirkt die Wahl nur auf die Berechnung.

### Brennstoff Briketts

Den Brennstoff wählst du auf der Startseite (Holz oder Briketts) oder in Home Assistant mit `select.kamin_app_brennstoff`. Er gilt je Auflage und lässt sich in jeder Phase umstellen, ohne dass eine Phase neu beginnt. Am besten stellst du um, bevor du die Tür öffnest. Bei Briketts und Holz zusammen wählst du Briketts.

Mit Briketts zündet die App mit ganz offener Klappe, wartet länger auf den Abbrand, regelt mit mindestens 50 % und hält die Glut mit 25 %. Braunkohle glüht bis zu neun Stunden nach und bildet ohne Luft viel Kohlenmonoxid, auch wenn das Abgas schon kalt wirkt. Deshalb bleibt die Klappe nach dem letzten Brikett-Feuer mindestens auf der Glut-Stellung, bis der Ofen kalt ist und der Nachlauf (Vorgabe 10 h) vorbei ist. Das gilt auch nach dem Zurückstellen auf Holz. Danach stellt die App selbst auf Holz zurück. Die Regler am Ofen stellst du von Hand: bei Briketts Automatikregler auf 2 Uhr und Sekundärluft 30 %, bei Holz zurück auf 5 Uhr und 45–50 %. Ein CO-Melder im Aufstellraum gehört dazu. Im Lauschen wirkt die Wahl nur auf die Berechnung.

Unter Einstellungen → Apps stehen nur `log_stufe` und `helfer_praefix`. Fehlen die Helfer einer Live-Automation, startet die App nach einer Minute im Standby mit ihren Vorgaben.

## Regelung und Parameter erklärt

Dieser Abschnitt erklärt die Begriffe und was jede Einstellung bewirkt. Die Zahlen in Klammern sind Vorgaben; was bei dir gilt, zeigt die App am Feld.

### Begriffe

| Begriff | Bedeutung |
|---|---|
| Abgastemperatur (T) | Messwert am Rauchrohr. Die Regelgröße: Die App führt sie im Abbrand auf das Ziel. |
| Ziel | Die gewünschte Abgastemperatur im Abbrand (`t_ziel`, über die Leistungsknöpfe Eco/Normal/Power gesetzt). Höher heißt mehr Leistung und schnelleres Abbrennen, niedriger heißt sparsamer und länger, aber zu niedrig verrußt die Scheibe. |
| Regelabweichung | Ziel minus Abgastemperatur. Positiv: zu kalt, die Klappe geht auf. Negativ: zu heiß, die Klappe geht zu. |
| Klappe (Stellgröße) | Öffnung der Zuluftklappe in %. 0 % zu, 100 % ganz offen. Mehr Luft heißt im Abbrand mehr Flamme und mehr Abgastemperatur. |
| Phase | Abschnitt des Feuers: Standby, Tür offen, Anheizen, Abbrand, Ausbrand, Glut, Abkühlen, dazu Anheizfehler, Störung, Wartung. Geregelt wird nur im Abbrand. In den anderen Phasen gilt eine feste Stellung. |
| Totzeit | Wie lange es dauert, bis eine Klappenbewegung am Abgasfühler ankommt. An deinem Ofen gemessen: 35–80 s. Je länger, desto vorsichtiger muss ein Regler sein. |
| Streckenverstärkung | Wie viele Kelvin die Abgastemperatur je % Klappe ändert. Gemessen: 0,7 bis 2,7 K/%, je nach Holz und Abbrandstand. Bei hoher Verstärkung schwingt ein scharfer Regler leicht. |
| Schwingung | Die Temperatur pendelt regelmäßig um das Ziel, weil der Regler zu spät und zu stark nachsteuert. Typisch hier: 3 min Periode, ±10 K, die Klappe pendelt mit. |
| P-Anteil, Verstärkung Kp | Sofortige Reaktion auf die Abweichung: Klappe ändert sich um Kp × Abweichung. Kp 0,3 %/K heißt: 10 K zu heiß, also 3 % weniger Klappe. Größer reagiert schneller, schwingt aber eher. Kleiner ist ruhiger, aber träger. |
| I-Anteil, Nachstellzeit Tn | Der Teil der Klappe, der eine bleibende Abweichung langsam wegarbeitet. Er gleicht aus, dass das Holz im Lauf des Abbrands mehr Luft braucht. Kürzeres Tn arbeitet schneller nach, macht aber unruhiger. Längeres Tn ist ruhiger, die Temperatur bleibt dann länger neben dem Ziel. Der I-Anteil steht im Experten-Modus auf der Startseite. |
| D-Anteil, Vorhalt Kd | Nur bei „Klassisch“. Reagiert auf den Anstieg der letzten 2 min. Der Anstieg hinkt etwa 1 min nach, deshalb verstärkt Kd bei 3–4 min Periode die Schwingung. Empfohlen: 0. |
| Anti-Windup | Steht die Klappe an ihrer Grenze (mindestens/höchstens), wächst der I-Anteil nicht weiter. So überschießt der Regler danach nicht. |
| Startwert | Mit dieser Klappenstellung (I-Anteil) beginnt der Regler beim Eintritt in den Abbrand, abhängig davon, wie heiß der Ofen beim Anheizen war. |
| Schwingungswächter | Nur bei 2.0. Er zählt Umkehrpunkte der Abgastemperatur: Ein Umkehrpunkt gilt, wenn die Temperatur danach mindestens 8 K zurückläuft. Drei gleichmäßige Halbwellen von 40–300 s heißen Schwingung. |
| Dämpfung | Nur bei 2.0. Faktor, mit dem der Wächter den Regler beruhigt: Kp wirksam = Kp × Dämpfung, Tn wirksam = Tn ÷ √Dämpfung. Bei 100 % gelten die Grundwerte. Je Schwingung sinkt sie auf 70 % des bisherigen Werts, nie unter 30 %. Nach 15 min ruhigem Abbrand steigt sie um 10 %. Gelernt wird nur im Betrieb, und sie bleibt über Abbrände und Neustarts erhalten. |
| Lauschen / Betrieb | Im Lauschen rechnet die App nur mit, die Klappe stellt die Automation in HA. Im Betrieb stellt die App die Klappe. |

### Regelstrategie

- **Klassisch:** PI-Regler mit optionalem Vorhalt und festen Werten (Kp, Tn, Kd). Auto-Tune „Aus dem Verlauf“ schlägt bei Schwingung ruhigere Werte vor, du bestätigst sie.
- **Regelstrategie 2.0:** PI-Regler ohne Vorhalt mit Grundwerten Kp 0,3 %/K und Tn 2 min. Der Schwingungswächter dämpft selbst, sobald die Temperatur pendelt, und gibt nach ruhiger Zeit wieder frei. Grund: Dein Ofen reagiert je nach Holz bis zu viermal unterschiedlich stark auf die Klappe. Feste Werte wären entweder für das eine Holz zu scharf oder für das andere zu träge.

Was du bei 2.0 beobachten kannst: Sinkt die Dämpfung bei jedem Abbrand auf 30 %, ist der Grundwert Kp zu hoch; stelle ihn etwas niedriger. Bleibt sie immer bei 100 % und die Temperatur liegt lange unter dem Ziel, darf Kp etwas höher oder Tn etwas kürzer sein. Nach einer Änderung der Grundwerte lohnt sich „Dämpfung auf 100 % zurücksetzen“.

### Regler (Einstellungen → Regler)

| Einstellung | Wirkung |
|---|---|
| Regelstrategie | Klassisch oder 2.0, gilt ab dem nächsten Takt (5 s). |
| Verstärkung Kp (2.0: 0,3 %/K) | Höher reagiert schneller auf Abweichungen und schwingt eher. Niedriger ist ruhiger und träger. |
| Nachstellzeit Tn (2.0: 2 min) | Kürzer: Bleibende Abweichungen sind schneller weg, aber unruhiger. Länger: ruhiger, folgt dem Abbrand langsamer. |
| Vorhalt Kd (nur Klassisch) | Auf 0 lassen (siehe Begriffe). |

### Parameter (Einstellungen → Parameter)

**Anheizen**

| Einstellung | Wirkung |
|---|---|
| Abbrand beginnt bei | Ab dieser Abgastemperatur wechselt das Feuer vom Anheizen in den Abbrand und die Regelung beginnt. Höher: länger mit viel Luft anheizen. Niedriger: früher regeln, das Feuer kann zu früh gedrosselt werden. |
| Anstieg für Feuererkennung | So schnell (K/min) muss die Temperatur steigen, damit die App ein Feuer erkennt. Höher verhindert Fehlalarme, erkennt aber später. |
| Mindestanstieg nach dem Öffnen / Zeit dafür | Steigt die Temperatur nach dem Türöffnen nicht so weit in dieser Zeit, war es kein Anheizen (Fehlstart), und die App kehrt still zurück. |
| Mindestdauer kalter Ofen / beim Nachlegen / auf heißem Feuer | So lange bleibt das Anheizen mindestens, auch wenn die Temperatur schon reicht. Länger gibt dem Holz mehr Zeit zum Durchzünden. |
| Höchstdauer Anheizen | Wird der Abbrand bis dahin nicht erreicht, meldet die App einen Anheizfehler. |
| Klappe beim Kaltstart / Kaltstart offen bis | Kaltstart: Mit dieser Stellung zündet der kalte Ofen. Sie gilt bis zur angegebenen Abgastemperatur. |
| Klappe beim Nachlegen | Stellung beim Nachlegen auf einen heißen Ofen. Weniger Luft verhindert, dass die frische Auflage durchschießt. |
| Ofen gilt als kalt unter | Darunter darf die Klappe ganz schließen (Standby). |

**Abbrand**

| Einstellung | Wirkung |
|---|---|
| Zieltemperatur, Ziel bei Eco/Normal/Power | Siehe Begriff „Ziel“. Die Leistungsknöpfe wählen einen dieser Werte. |
| Klappe mindestens | Untergrenze der Regelung. Höher hält die Flamme sicher, auch wenn es zu heiß wird. Niedriger drosselt stärker, das Feuer kann aber ersticken und CO bilden (unter 5 % nur nach Rückfrage). Liegt die Klappe oft an dieser Grenze und die Temperatur trotzdem über dem Ziel, ist das Ziel für dieses Holz zu niedrig. |
| Klappe höchstens | Obergrenze der Regelung. |
| Klappe mindestens beim Entgasen / Dauer der Entgasung | In den ersten Minuten nach dem Auflegen gast das Holz stark aus und braucht mehr Luft. So lange gilt die höhere Untergrenze. |
| Schließgeschwindigkeit | So viel % darf die Klappe je 30 s höchstens zugehen. Langsamer verhindert, dass ein heißer Moment das Feuer abwürgt. Öffnen ist nicht begrenzt. |
| Startwert kalter / heißer Ofen | Klappe, mit der der Regler im Abbrand beginnt. Passt der Startwert, gibt es beim Übergang weniger Überschwingen. |

**Glut**

| Einstellung | Wirkung |
|---|---|
| Abfall für Ausbrand | So weit muss die Temperatur vom Höchstwert fallen, bis der Abbrand als Ausbrand gilt. Dann hält die App die Klappe konstant offen, statt nachzuregeln. |
| Glut beginnt unter / Abkühlen beginnt unter | Temperaturgrenzen für die Phasen Glut und Abkühlen. |
| Klappe in der Glut | Wenig Luft hält die Glut lange, mehr Luft brennt sie schneller ab. |
| Höchstdauer Glut / Abkühlen | Danach wechselt die Phase von selbst. |
| Puffer gilt als voll ab | Ab dieser Puffertemperatur rät die App vom Nachlegen ab. |

**Briketts:** wie Holz, mit eigenen Werten für Braunkohlebriketts (siehe „Brennstoff Briketts“).

**Sicherheit**

| Einstellung | Wirkung |
|---|---|
| Übertemperatur ab / Klappe bei Übertemperatur | Darüber drosselt die App sofort auf diese Stellung, in jeder Phase. |
| Wasser höchstens / Klappe bei heißem Wasser | Schützt den Wasserkreis. Ist das Wasser zu heiß, begrenzt die App die Klappe auf diese Stellung. |
| Abgasfühler | **LEDA LUC:** Der LUC meldet den Abgaswert nur, wenn er sich ändert. Ein stehender Wert ist deshalb kein Ausfall, solange Druck oder CAN-Alter vom LUC frisch sind. **Eigener Fühler:** Ein eigener Fühler (zum Beispiel ein ESPHome-Thermoelement) meldet regelmäßig. Dann zählt nur sein eigenes Alter, der LUC kann einen toten Fühler nicht mehr überdecken. |
| Fühler gilt ohne Feuer als stumm nach | Bleibt der Abgaswert ohne Feuer (Standby, Glut, Abkühlen) so lange ohne Meldung, gilt der Fühler als stumm. Ohne Feuer folgt daraus nur dann eine Störung, wenn das Wasser am Ofen steigt. Home Assistant meldet nur geänderte Werte, auch ein lebender Fühler kann in Ruhe mehrere Minuten still wirken. Deshalb nicht unter 300 s. |
| Fühler gilt bei Feuer als stumm nach | Bei Feuer (Anheizen, Abbrand, Ausbrand) geht die App nach dieser Zeit ohne Meldung in **Störung** und öffnet die Klappe ganz. Kleiner heißt schneller sicher, aber eine falsche Störung wird wahrscheinlicher. Maßgeblich ist die längste Pause, in der der Fühler nichts schickt. Viele ESPHome-Fühler senden nur bei einer Änderung (etwa ab 1 K) und sonst spätestens alle 60 s; fällt die Temperatur langsam, kommt dann nur ein Wert pro Minute. Faustregel: doppelte längste Pause, also 120 s (Vorgabe). Einen Defekt meldet die Rolle „Abgasfühler Fehler“ ohnehin sofort. |
| Abgasfühler Fehler (Ein-/Ausgänge) | Optionale Rolle für einen Fehlermelder des Fühlers (an = Defekt), zum Beispiel `binary_sensor.…_abgasfuhler_fehler` eines MAX31856. Viele Fühler verwerfen ungültige Messungen, ihr Wert bleibt dann einfach stehen. Ist der Melder an, gilt der Fühler sofort als stumm: Bei Feuer folgt die Störung mit offener Klappe, im Betrieb fällt die App auf Lauschen zurück. |
| Störung bei ausgefallenem Fühler nach | Ist der Abgasfühler in Home Assistant „nicht verfügbar“ oder „unbekannt“ (Funk weg, Gerät neu gestartet, Thermoelement offen), geht die App nach dieser Zeit in Störung und öffnet die Klappe. Vorgabe 60 s. |
| Feuer am Wasser erkannt ab | Steigt das Wasser in 10 min so weit, nimmt die App auch ohne Abgasanstieg ein Feuer an. |
| Wartung endet nach / Klappe in der Wartung / Klappe im Handbetrieb | Feste Stellungen für Reinigung und Handbetrieb. |

**Wasserseite und Wartung:** Grenzen für Warnungen zu Rücklauf, Vorlauf und Pumpe sowie die Erinnerung zur Reinigung des Wärmetauschers. Sie stellen die Klappe nicht.

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
| `sensor.kamin_app_wt_reinigung_rest` | Brennstunden bis zur Reinigung des Wärmetauschers |
| `sensor.kamin_app_wasser_status` | `ok`, `hinweis`, `warnung` oder `alarm`, Texte als Attribut |
| `sensor.kamin_app_amortisation` | Amortisation |

`p` steht für `paritaet`, `n` für `neu`. Die Sensoren werden bei Änderung und sonst mindestens alle 60 Sekunden geschrieben. Es sind einfache Zustände ohne Entitätsregistrierung. Nach einem Neustart von Home Assistant fehlen sie, bis die App sie wieder schreibt.

## Verlauf

Je Tag entsteht `/config/verlauf/verlauf-JJJJ-MM-TT.jsonl` (auf dem Host unter `/addon_configs/`) mit einer JSON-Zeile je Lauf. Dateien älter als 30 Tage löscht die App. Die Dateien sind von Sicherungen ausgenommen.

## Grenzen

- Ohne Verbindung zu Home Assistant rechnet und schaltet die App nicht. Sie verbindet sich neu, die Wartezeit steigt bis 60 Sekunden.
- Im Lauschen übernimmt sie die Zustände nur beim ersten Start aus den Helfern einer Live-Automation und rechnet danach eigenständig weiter.
- Die Oberfläche ist derzeit nur auf Deutsch.
