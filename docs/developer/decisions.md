# Architekturentscheidungen (Warum)

Version-Single-Source: `pyproject.toml` (`0.2`)

## 1) Lokal-first, Self-Hosted mit Login-Basis
Entscheidung:
- lokale Python-Web-App mit verpflichtender Anmeldung,
- kein vollständiges Rollen-/Rechtesystem in v1,
- Architektur offen für spätere Mehrbenutzer-Erweiterung.

Warum:
- geringe Betriebskomplexität,
- schnelle Iteration mit direktem Nutzerfeedback,
- Datenschutz/Dateien bleiben lokal.
- Zugangsschutz ist auch im LAN ein realistisches Basisbedürfnis.

## 2) SQLite + SQLModel
Entscheidung:
- SQLite als persistente Datenbasis,
- SQLModel/SQLAlchemy als ORM-Schicht.

Warum:
- kein externer DB-Server nötig,
- ausreichend für lokale Last,
- klare Migrations-/Seed-Logik im App-Start.

## 3) Hard-Reset bei Schema-Versionwechsel
Entscheidung:
- bei Marker-Mismatch wird DB + Archiv komplett neu aufgebaut.

Warum:
- frühe Produktphase, schneller Umbau ohne komplexe Legacy-Migration.
- reduziert Risiko inkonsistenter Altdaten bei grossen Modellwechseln.

Konsequenz:
- für spätere stabile Releases sollte dieses Verhalten durch echte Migrationen ersetzt werden.

## 4) Kostenzuordnung als fachliche Wahrheit
Entscheidung:
- keine direkte fachliche „Beleg->Projekt/Kategorie“-Wahrheit,
- stattdessen `cost_allocation` als zentrale Verteilungsebene.

Warum:
- Split-Fähigkeit,
- klare Summenregeln,
- auswertbare Struktur für Kategorie-/Unterkategorie-Reports.

## 5) Versteckte technische Kostenstelle
Entscheidung:
- wenn kein Projekt gesetzt ist, wird intern `Allgemeine Ausgabe` als `cost_area` gesetzt.

Warum:
- UI bleibt simpel (Projekt optional),
- Daten bleiben dennoch technisch vollständig und konsistent.

## 6) OCR im Background-Thread
Entscheidung:
- OCR über `OCRJobQueue` im Worker-Thread.

Warum:
- UI bleibt reaktiv,
- lange OCR-Laufzeiten blockieren nicht die Bedienung,
- Statusmodell (`queued/running/done/error`) bleibt transparent.

## 7) FTS5 für Suche
Entscheidung:
- Volltextsuche über SQLite FTS5 (`receipt_fts`).

Warum:
- gute Suchperformance ohne externe Suchinfrastruktur,
- passend für lokale Deploymentform.

## 8) Integer-Cents für Geld
Entscheidung:
- Geldwerte ausschließlich als Integer-Cents speichern.

Warum:
- stabile Berechnungen ohne Float-Rundungsfehler,
- verlässliche Summenvalidierung über Allokationen.

## 9) AGPL + Rechtsinfos in App
Entscheidung:
- Projektlizenz AGPL-3.0-or-later,
- Copyright und Fremdlizenz-Dialog in Einstellungen.

Warum:
- Open-Source-Nutzung sauber vorbereiten,
- rechtliche Transparenz direkt in der Anwendung.

## 10) Sicherheits-Baseline pragmatisch statt Enterprise
Entscheidung:
- Authentifizierung mit `app_user` + Argon2id-Hashing,
- einmalige Setup-Seite mit Setup-Token aus Server-Log,
- Session-Timeouts (Idle + absolut) und Login-Lockout bei Fehlversuchen,
- Trusted-Host/Origin-Checks und grundlegende Security-Header,
- Upload-Härtung inkl. Inhaltsprüfung und Größenlimit.

Warum:
- adressiert die wichtigsten Risiken einer self-hosted Web-App,
- bleibt überschaubar und wartbar,
- verbaut keine spätere Mehrbenutzer-Entwicklung.
