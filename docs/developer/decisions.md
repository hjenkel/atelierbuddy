# Architekturentscheidungen (Warum)

Version-Single-Source: `pyproject.toml` (`0.1.0`)

## 1) Lokal-first, Single-User
Entscheidung:
- lokale Python-Web-App ohne Login/Mehrbenutzer im aktuellen Produktstand.

Warum:
- geringe Betriebskomplexitaet,
- schnelle Iteration mit direktem Nutzerfeedback,
- Datenschutz/Dateien bleiben lokal.

## 2) SQLite + SQLModel
Entscheidung:
- SQLite als persistente Datenbasis,
- SQLModel/SQLAlchemy als ORM-Schicht.

Warum:
- kein externer DB-Server noetig,
- ausreichend fuer lokale Last,
- klare Migrations-/Seed-Logik im App-Start.

## 3) Hard-Reset bei Schema-Versionwechsel
Entscheidung:
- bei Marker-Mismatch wird DB + Archiv komplett neu aufgebaut.

Warum:
- fruehe Produktphase, schneller Umbau ohne komplexe Legacy-Migration.
- reduziert Risiko inkonsistenter Altdaten bei grossen Modellwechseln.

Konsequenz:
- fuer spaetere stabile Releases sollte dieses Verhalten durch echte Migrationen ersetzt werden.

## 4) Kostenzuordnung als fachliche Wahrheit
Entscheidung:
- keine direkte fachliche „Beleg->Projekt/Kategorie“-Wahrheit,
- stattdessen `cost_allocation` als zentrale Verteilungsebene.

Warum:
- Split-Faehigkeit,
- klare Summenregeln,
- auswertbare Struktur fuer Kategorie-/Unterkategorie-Reports.

## 5) Versteckte technische Kostenstelle
Entscheidung:
- wenn kein Projekt gesetzt ist, wird intern `Allgemeine Ausgabe` als `cost_area` gesetzt.

Warum:
- UI bleibt simpel (Projekt optional),
- Daten bleiben dennoch technisch vollstaendig und konsistent.

## 6) OCR im Background-Thread
Entscheidung:
- OCR ueber `OCRJobQueue` im Worker-Thread.

Warum:
- UI bleibt reaktiv,
- lange OCR-Laufzeiten blockieren nicht die Bedienung,
- Statusmodell (`queued/running/done/error`) bleibt transparent.

## 7) FTS5 fuer Suche
Entscheidung:
- Volltextsuche ueber SQLite FTS5 (`receipt_fts`).

Warum:
- gute Suchperformance ohne externe Suchinfrastruktur,
- passend fuer lokale Deploymentform.

## 8) Integer-Cents fuer Geld
Entscheidung:
- Geldwerte ausschliesslich als Integer-Cents speichern.

Warum:
- stabile Berechnungen ohne Float-Rundungsfehler,
- verlässliche Summenvalidierung ueber Allokationen.

## 9) AGPL + Rechtsinfos in App
Entscheidung:
- Projektlizenz AGPL-3.0-or-later,
- Copyright und Fremdlizenz-Dialog in Einstellungen.

Warum:
- Open-Source-Nutzung sauber vorbereiten,
- rechtliche Transparenz direkt in der Anwendung.
