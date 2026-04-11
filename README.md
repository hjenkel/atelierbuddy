# Atelier Buddy

Lokale Python-Web-App fuer die kreative Studioverwaltung von selbststaendigen Kuenstler:innen.

Aktuelle Version: `0.1.0` (pre-alpha)

## Features
- Batch-Import von Belegen (PDF, JPG, PNG, HEIC) direkt in der Belege-Seite
- OCR mit `ocrmypdf` (deu+eng)
- Volltextsuche via SQLite FTS5
- Kostenzuordnung pro Beleg mit Kostenkategorie + Unterkategorie + optionalem Projekt
- Wenn kein Projekt gewählt wird, erfolgt intern automatisch die Zuordnung zur Kostenstelle `Allgemeine Ausgabe`
- Standardmodus mit 100%-Zuordnung und optionaler Split-Aufteilung
- Lieferanten-Stammdaten und Lieferantenzuordnung (genau ein Lieferant pro Beleg)
- Rechnungsbetrag mit Brutto/USt/Netto
- Cover-Foto pro Projekt (optimiertes WebP)
- Projektdatum pro Projekt (optional)
- Kostenkategorien mit Symbolauswahl (selbst anlegbar)
- Unterkategorien pro Kostenkategorie (1:n), inkl. nicht löschbarem Standard `Allgemein`
- Starter-Set Kostenkategorien: Material, Software, Miete, Werbung, Reisen, Weiterbildung, Sonstiges
- Beleg-Vollstaendigkeitsstatus (Pflichtfelder)
- Thumbnail-Archiv und Belegdetail als Vollseite (Originaldatei links, Indexierung rechts)

## Voraussetzungen
- Python 3.12+
- `ocrmypdf` im Systempfad

macOS (Beispiel):
```bash
brew install ocrmypdf tesseract ghostscript
brew install tesseract-lang
```

## Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Start
```bash
python -m belegmanager
```

App-URL: `http://127.0.0.1:8080`

## Docker (empfohlen)
```bash
docker compose up --build -d
```

App-URL (Docker): `http://localhost:12321`

Nutzliche Befehle:
```bash
# Status/Logs
docker compose ps
docker compose logs -f

# Neustart/Stop
docker compose restart
docker compose down

# Update nach Codeaenderungen
docker compose up --build -d
```

Hinweis:
- Persistente Daten liegen im Docker Named Volume `atelier_buddy_data` (`/app/data` im Container).
- Lokaler Python-Start ohne Docker bleibt weiterhin moeglich.

## Hinweise
- Die App arbeitet lokal und speichert Daten in `data/belegmanager.db`.
- Importierte Dateien werden nach `data/archive` kopiert.
- Bei internem Schema-Marker-Wechsel (`db.py` -> `SCHEMA_VERSION`) wird ein automatischer Full-Reset ausgefuehrt (DB + Archiv), da kein Alt-Daten-Mapping verwendet wird.
- Falls `ocrmypdf` fehlt, werden OCR-Jobs mit Fehlermeldung markiert.
- Falls `deu` in Tesseract fehlt, laeuft OCR mit verfuegbaren Sprachen weiter (z. B. `eng`) und zeigt einen Setup-Hinweis.
- Bei textbasierten PDFs wird bei `OCR skipped ...` der PDF-Textlayer fuer Suche/FTS uebernommen.

## Versionierung
- Single Source of Truth: `pyproject.toml` -> `[project].version`
- Laufzeitanzeige in der App (Seite `Einstellungen`) nutzt diese Version.
- Changelog wird in `CHANGELOG.md` gefuehrt.
- Empfehlung fuer pre-alpha: `0.1.x` fuer Bugfixes, `0.2.0` fuer neue Features.

## Weiterfuehrende Entwicklerdoku
- Einstieg: [docs/README.md](docs/README.md)
- Die Dokumentation orientiert sich an der Version aus `pyproject.toml` (aktuell `0.1.0`).

## Lizenz & Rechtliches
- Projektlizenz: `AGPL-3.0-or-later` (siehe `LICENSE`)
- Copyright: `Copyright (c) 2026 Hanno Jenkel`
- In der App unter `Einstellungen` gibt es den Link `Fremdlizenzen` fuer die Uebersicht aller Python-Abhaengigkeiten (inkl. transitiv) mit Lizenzinformationen.
- Optionaler Neuaufbau des Lizenz-Caches:
  ```bash
  source .venv/bin/activate
  python -c "from belegmanager.legal import get_third_party_notices; get_third_party_notices(force_refresh=True)"
  ```
