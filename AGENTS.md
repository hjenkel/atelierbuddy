# AGENTS.md

## Projektziel
Atelier Buddy ist eine lokale Belegverwaltung für Künstler:innen, Bands und Kreative mit Fokus auf:
- einfache Erfassung/Zuordnung von Belegen
- Vorbereitung für EÜR-Auswertungen
- kein Fokus auf USt-Voranmeldung/ELSTER
- Alpha-Vorbereitung mit Schutz persistenter Nutzerdaten bei zukünftigen Updates

## Tech-Stack
- Python 3.12
- NiceGUI (UI)
- SQLModel/SQLAlchemy + SQLite
- OCR: ocrmypdf + tesseract + ghostscript
- Pillow/pypdfium2 (Thumbnails, Bild/PDF-Verarbeitung)
- Docker Compose für empfohlenen Betrieb

## Start / Test / Lint / Build
- Lokal starten: `python -m belegmanager`
- Lokal (venv) testen: `./.venv/bin/python -m pytest -q`
- Docker (empfohlen): `docker compose up --build -d` (App auf `http://localhost:12321`)
- Lint: aktuell kein eigener Lint-Befehl im Repo konfiguriert
- Packaging-Build: setuptools (`pyproject.toml`), Installation lokal via `python -m pip install -e .`

## Architekturgrenzen
- Entry Point und Runtime-Konfig: `belegmanager/main.py`, `belegmanager/config.py`
- UI nur in `belegmanager/ui/*` (Seiten/Theme), Fachlogik in `belegmanager/services/*`
- Datenmodell in `belegmanager/models.py`, DB-Setup und interne Migrationen in `belegmanager/db.py`
- Fachliche Zuordnungs-Wahrheit liegt in `cost_allocation` (nicht parallel in UI-Nebenfeldern)
- Geldwerte immer als `*_cents` (Integer), nicht als Float speichern
- Version-Single-Source-of-Truth: `pyproject.toml` (`[project].version`)
- Persistente Laufzeitdaten liegen unter `data/` (DB + Archiv)
- Schemaänderungen dürfen keine Nutzerdaten oder Archivdateien automatisch löschen

## Do-not-Rules
- Keine Geschäftslogik in UI-Eventhandler duplizieren; Services nutzen
- Keine zweite Wahrheitsquelle neben Allokationen einführen
- Keine Vorzeichenregeln verletzen:
  - `invoice` => Brutto `>= 0`
  - `credit_note` => Brutto `<= 0`
- Keine OCR-Text-/OCR-Status-Anzeige wieder in die UI zurückbringen
- Keine lokalen Nutzerdaten in Git tracken (`data/belegmanager.db`, `data/archive/` bleiben ignoriert)

## Done-Kriterien
Änderung gilt als fertig, wenn:
1. App lokal startet (`python -m belegmanager`) ohne Runtime-Fehler
2. Tests grün sind (`./.venv/bin/python -m pytest -q`)
3. Betroffene Kernflüsse manuell funktionieren (Beleg öffnen/speichern, Zuordnung, Suche/Filter)
4. Keine neuen getrackten Nutzerdaten unter `data/` auftauchen
5. Bei Verhaltensänderungen Doku in `README.md` oder `docs/developer/*` nachgezogen ist
6. Datenbankstart und Migrationen sind ohne automatischen Datenverlust für DB und Archiv verifiziert
