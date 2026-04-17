# Architekturentscheidungen

Version-Single-Source: `pyproject.toml` (`0.2.2`)

## 1. Lokal-first und self-hosted
Entscheidung:
- lokale Python-Web-App
- Daten und Dateien bleiben im eigenen Betrieb
- kein Cloud-Zwang

Warum:
- passt gut zu kleinen kreativen Setups
- hält Infrastruktur und Datenschutz pragmatisch
- erlaubt schnelle Iteration ohne externe Plattformabhängigkeit

## 2. SQLite plus SQLModel
Entscheidung:
- SQLite als persistente Datenbasis
- SQLModel/SQLAlchemy als Modell- und Query-Schicht

Warum:
- kein externer Datenbankserver nötig
- ausreichend für den erwarteten lokalen Einsatz
- Entwicklung bleibt leichtgewichtig

## 3. Hard Reset bei Schema-Wechsel in der frühen Phase
Entscheidung:
- bei Marker-Mismatch werden DB und Archiv aktuell komplett neu aufgebaut

Warum:
- das Produkt befindet sich noch in einer Phase mit schnellen Modelländerungen
- komplexe Migrationen würden aktuell mehr Last als Nutzen erzeugen

Konsequenz:
- für spätere stabilere Releases sollte dieses Verhalten durch echte Migrationen ersetzt werden

## 4. Ausgabenlogik über `cost_allocation`
Entscheidung:
- Ausgaben werden fachlich nicht direkt an einen Belegkopf gebunden
- stattdessen bildet `cost_allocation` die zentrale Verteilungsebene

Warum:
- unterstützt Split-Zuordnungen
- sichert Summenkonsistenz
- bildet eine belastbare Basis für Reports

## 5. Verkauf und Rechnung sind in v0.2 derselbe Datensatz
Entscheidung:
- es gibt kein separates Rechnungsobjekt
- `sales_order` modelliert den Verkauf und bei gesetztem Rechnungsdatum zugleich die Ausgangsrechnung

Warum:
- hält die Modellkomplexität für den aktuellen Bedarf niedriger
- deckt den praktischen Kernworkflow bereits gut ab

Konsequenz:
- spätere Erweiterungen wie Zahlungseingänge, Teilzahlungen oder automatische Dokumenterzeugung können ein eigenes Modell erfordern

## 6. Rechnungsdokument direkt am Verkauf
Entscheidung:
- ein Verkauf kann genau ein Rechnungsdokument referenzieren
- Upload/Ersetzen passiert direkt auf der Verkaufsdetailseite
- kein OCR, kein ImportBatch und keine Beleg-Erstellung für diese Datei

Warum:
- Ausgangsrechnungen sollen nachvollziehbar mit Datei abgelegt werden
- die Datei ist Teil des Verkaufsstatus, aber kein eigener Belegworkflow

## 7. Einnahmenauswertung nach Rechnungsdatum

Entscheidung:
- der Einnahmenreport wertet aktuell nach `invoice_date` aus
- nicht nach Zahlungseingängen
- nicht nach Dokumentstatus

Warum:
- es gibt derzeit keine separate Zahlungstabelle
- der Report bleibt dadurch fachlich konsistent zum vorhandenen Datenmodell

## 8. Löschschutz für fakturabezogene Verkäufe
Entscheidung:
- Verkäufe mit `invoice_date`, `invoice_number` oder Rechnungsdokument können weder archiviert noch endgültig gelöscht werden

Warum:
- schützt vor dem Entfernen bereits fakturierter Vorgänge
- passt besser zu kaufmännischer Nachvollziehbarkeit als ein freies Löschen

## 9. Personenzentrierte Kontakte
Entscheidung:
- Kontakte bleiben personenzentriert
- mindestens Vorname oder Nachname ist erforderlich

Warum:
- passt zum bestehenden UI- und Datenmodell
- vermeidet eine zweite Organisationslogik im Kontaktbereich

## 10. OCR im Hintergrund
Entscheidung:
- OCR läuft über `OCRJobQueue` im Worker-Thread

Warum:
- lange OCR-Läufe blockieren die UI nicht
- Statuswechsel bleiben nachvollziehbar

## 11. FTS5 für lokale Suche
Entscheidung:
- Volltextsuche über SQLite FTS5

Warum:
- gute lokale Suchperformance
- kein externer Suchdienst nötig

## 12. Integer-Cents und Decimal-Mengen
Entscheidung:
- Geldwerte als Integer-Cents
- Verkaufsmengen als Decimal mit drei Nachkommastellen

Warum:
- robuste Rundung und Summenbildung
- praxistauglich für Stückzahlen, Zeiten und Teilmengen

## 13. Pragmatic Security statt Enterprise-Stack
Entscheidung:
- Setup-Token, Login, Argon2id, Session-Timeouts, Host-/Origin-Prüfung und Upload-Härtung als Basis

Warum:
- deckt die wichtigsten Risiken einer self-hosted Web-App ab
- bleibt überschaubar und wartbar
