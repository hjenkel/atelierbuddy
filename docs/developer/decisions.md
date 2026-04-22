# Architekturentscheidungen

Version-Single-Source: `pyproject.toml` (`0.3.4`)

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

## 3. Versionierte interne Migrationen für die Alpha-Phase
Entscheidung:
- Schemaänderungen laufen über interne, versionierte Migrationen
- bei nicht sicher migrierbarer oder inkonsistenter Alt-Datenbank blockiert der Start

Warum:
- Alpha-Vorbereitung braucht belastbaren Schutz für persistente Nutzerdaten
- das aktuelle SQLite-/SQLModel-Setup lässt sich mit einem leichten internen Migrationsrahmen pragmatisch absichern
- automatische Datenlöschung wäre für reale Nutzung zu riskant

Konsequenz:
- `data/belegmanager.db` und `data/archive/` werden bei Schemaänderungen nie automatisch gelöscht
- Migrationen werden in einer Metatabelle nachgehalten und beim Start in Reihenfolge ausgeführt
- Fehler im Migrations- oder Validierungspfad stoppen die App lieber klar, als Daten unbemerkt zu verlieren

## 4. Ausgabenlogik über `cost_allocation`
Entscheidung:
- Ausgaben werden fachlich nicht direkt an einen Belegkopf gebunden
- stattdessen bildet `cost_allocation` die zentrale Verteilungsebene
- unvollständige Zuordnungen dürfen als `draft` gespeichert werden, fachlich wirksam sind aber nur `posted`-Zeilen

Warum:
- unterstützt Split-Zuordnungen
- erlaubt Entwurfsstände ohne Datenverlust
- sichert Summenkonsistenz
- bildet eine belastbare Basis für Reports

## 5. Verkauf und Rechnung bleiben auch in 0.3 derselbe Datensatz
Entscheidung:
- es gibt kein separates Rechnungsobjekt
- `sales_order` modelliert den Verkauf und bei gesetztem Rechnungsdatum zugleich die Ausgangsrechnung

Warum:
- hält die Modellkomplexität für den aktuellen Bedarf niedriger
- deckt den praktischen Kernworkflow bereits gut ab

Konsequenz:
- spätere Erweiterungen wie Zahlungseingänge oder Teilzahlungen können ein eigenes Modell erfordern
- die inzwischen umgesetzte automatische Dokumenterzeugung bleibt trotzdem am bestehenden Verkaufsobjekt aufgehängt

## 6. Rechnungsdokument direkt am Verkauf
Entscheidung:
- ein Verkauf kann genau ein Rechnungsdokument referenzieren
- Upload/Ersetzen passiert direkt auf der Verkaufsdetailseite
- automatische PDF-Erzeugung ersetzt denselben Dokument-Slot
- kein OCR, kein ImportBatch und keine Beleg-Erstellung für diese Datei

Warum:
- Ausgangsrechnungen sollen nachvollziehbar mit Datei abgelegt werden
- die Datei ist Teil des Verkaufsstatus, aber kein eigener Belegworkflow
- das hält UI, Datenmodell und Archivlogik einfacher als ein separates Dokumentobjekt

## 6a. Rechnungs-PDF als Snapshot über festen HTML/CSS-Renderer
Entscheidung:
- Rechnungen werden in v1 über ein mitgeliefertes HTML/CSS-Standardtemplate erzeugt
- Rendering läuft serverseitig über `WeasyPrint`
- Template-Dateien liegen getrennt vom Python-Code, sind aber noch nicht frei durch Nutzer bearbeitbar

Warum:
- trennt Rechnungsdaten, Vorlage und PDF-Erzeugung sauber
- hält die aktuelle Standardfunktion stabil
- bereitet spätere Template-Features vor, ohne schon eine freie Template-Engine zu öffnen

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
- einfacher First-Admin-Setup-Flow, Login, Argon2id, Session-Timeouts, Host-/Origin-Prüfung und Upload-Härtung als Basis

Warum:
- deckt die wichtigsten Risiken einer self-hosted Web-App ab
- bleibt überschaubar und wartbar

## 14. App-artiges Mobilverhalten statt freiem Browser-Zoom
Entscheidung:
- die Web-App nutzt global einen app-artigen Viewport mit deaktiviertem Browser-Seitenzoom
- mobile Bildvorschauen in der Belegdetailseite bekommen stattdessen gezieltes Pinch-to-Zoom im Viewer
- iOS-Homescreen-Integration nutzt ein eigenes `apple-touch-icon`

Warum:
- versehentliches Zoomen stört auf iPhone/iPad im Alltagsfluss stärker als es hilft
- die Belegvorschau braucht trotzdem eine direkte Zoom-Geste für Bilder
- Homescreen-Installation soll auf iOS konsistent als App wirken
