# Atelier Buddy

Lokale Python-Web-App für einfache Belegverwaltung in kreativen Arbeitskontexten.

Aktuelle Version: `0.1.0` (pre-alpha)

## Wofür ist das?
Atelier Buddy ist für **Künstler:innen, Bands und andere Kreative** gedacht, die ihre Ausgaben strukturiert erfassen wollen, ohne klassische Enterprise-Buchhaltungssoftware.

Fokus aktuell:
- einfache Belegverwaltung
- saubere Zuordnung von Ausgaben
- Vorbereitung für EUER-Auswertungen

Nicht im Fokus aktuell:
- USt-Voranmeldungen
- ELSTER-Integration
- rechtsverbindliche Steuererklärungs-Funktionen

## Wichtige Hinweise (Haftung, Nutzung, Verantwortung)
- Die Software wird **ohne Gewährleistung** bereitgestellt ("as is").
- Es wird **keine Haftung** für Datenverlust, Fehlberechnungen oder steuerliche Folgen übernommen.
- Die App hat **keine steuerliche oder rechtliche Prüfung** durch Steuerberatung/Kanzlei erhalten.
- Nutzung erfolgt in eigener Verantwortung.
- **Regelmäßige Backups sind dringend empfohlen.**

## Vibecoding-Transparenz
Dieses Projekt wurde komplett **vibecoded** erstellt.

Das heißt: Es kann fachliche, technische und sicherheitsrelevante Lücken geben.
Wenn du Erfahrung mit Python, Buchhaltungslogik, Security oder UX hast:
**Code-Reviews, Validierung und Verbesserungen sind sehr willkommen.**

## Features
- Batch-Import von Belegen (PDF, JPG, PNG, HEIC) direkt in der Belege-Seite
- OCR mit `ocrmypdf` (deu+eng)
- Volltextsuche via SQLite FTS5
- Kostenzuordnung pro Beleg mit Kostenkategorie + Unterkategorie + optionalem Projekt
- Wenn kein Projekt gewählt wird, erfolgt intern automatisch die Zuordnung zur Kostenstelle `Allgemeine Ausgabe`
- Standardmodus mit 100%-Zuordnung und optionaler Split-Aufteilung
- Anbieter-Stammdaten und Anbieterzuordnung
- Rechnungsbetrag mit Brutto/USt/Netto
- Cover-Foto pro Projekt (optimiertes WebP)
- Beleg-Vollständigkeitsstatus (Pflichtfelder)
- Thumbnail-Archiv und Belegdetail als Vollseite (Originaldatei links, Indexierung rechts)

## Schnellstart mit Docker (empfohlen)
### Voraussetzungen
- Docker Desktop (oder Docker Engine + Compose Plugin)

### Schritt-für-Schritt
```bash
# 1) In deinen gewünschten Zielordner wechseln (z. B. Projekt-, Docker- oder Home-Verzeichnis)
cd /pfad/zu/deinem/zielordner

# 2) Repository klonen
git clone https://github.com/hjenkel/atelierbuddy.git

# 3) In den Projektordner wechseln
cd atelierbuddy

# 4) Mit der im Repo enthaltenen docker-compose.yml starten
docker compose up --build -d
```

App-URL: `http://localhost:12321`

### Was wird dabei genutzt?
- Es wird die bereits im Repository enthaltene Datei `docker-compose.yml` verwendet.
- Vorkonfiguriert sind:
  - Port-Mapping `12321:8080`
  - Persistenz-Volume `atelier_buddy_data` nach `/app/data`
  - OCR-Sprachen `deu+eng`

Wichtig:
- Die GitHub-URL (`https://github.com/hjenkel/atelierbuddy`) gehört zum `git clone`-Schritt.
- Sie wird **nicht** in `docker-compose.yml` eingetragen.

### Betrieb im Alltag
```bash
# Status/Logs
docker compose ps
docker compose logs -f

# Neustart/Stop
docker compose restart
docker compose down

# Neu bauen + starten
docker compose up --build -d
```

### Update auf neue Version
```bash
git pull
docker compose up --build -d
```

### Docker-Backup-Hinweis
- Persistente Daten liegen im Docker Named Volume `atelier_buddy_data` (`/app/data` im Container).
- OCR-Binaries und Sprachpakete sind im Docker-Image enthalten.

### Empfohlener Betrieb
Empfohlen ist ein kleiner lokaler Host im Heim-/Studio-Netz, z. B. ein Raspberry Pi oder Mini-PC, auf dem Docker dauerhaft läuft.

## Lokale Installation (ohne Docker)
### Voraussetzungen
- Python 3.12+
- `ocrmypdf`, `tesseract`, `ghostscript` im Systempfad

macOS (Beispiel):
```bash
brew install ocrmypdf tesseract ghostscript
brew install tesseract-lang
```

Ubuntu/Debian (Beispiel):
```bash
sudo apt update
sudo apt install -y ocrmypdf tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng ghostscript
```

### Schritte
```bash
# 1) Projektordner
cd /pfad/zu/atelierbuddy

# 2) virtuelle Umgebung
python3.12 -m venv .venv
source .venv/bin/activate

# 3) Abhängigkeiten
python -m pip install -U pip
python -m pip install -e .

# 4) Start
python -m belegmanager
```

App-URL lokal: `http://127.0.0.1:8080`

## Daten & Backups
Lokale Daten liegen unter `data/`:
- `data/belegmanager.db`
- `data/archive/`

Einfaches Backup-Beispiel:
```bash
tar -czf atelierbuddy-backup-$(date +%Y-%m-%d).tar.gz data
```

## Hinweise
- Bei internem Schema-Marker-Wechsel (`db.py` -> `SCHEMA_VERSION`) wird ein automatischer Full-Reset ausgeführt (DB + Archiv), da kein Alt-Daten-Mapping verwendet wird.
- Falls `ocrmypdf` fehlt, werden OCR-Jobs mit Fehlermeldung markiert.
- Falls `deu` in Tesseract fehlt, läuft OCR mit verfügbaren Sprachen weiter (z. B. `eng`) und zeigt einen Setup-Hinweis.
- Bei textbasierten PDFs wird bei `OCR skipped ...` der PDF-Textlayer für Suche/FTS übernommen.

## Versionierung
- Single Source of Truth: `pyproject.toml` -> `[project].version`
- Laufzeitanzeige in der App (Seite `Einstellungen`) nutzt diese Version.
- Changelog wird in `CHANGELOG.md` geführt.
- Empfehlung für pre-alpha: `0.1.x` für Bugfixes, `0.2.0` für neue Features.

## Weiterführende Entwicklerdoku
- Einstieg: [docs/README.md](docs/README.md)
- Die Dokumentation orientiert sich an der Version aus `pyproject.toml` (aktuell `0.1.0`).

## Lizenz & Rechtliches
- Projektlizenz: `AGPL-3.0-or-later` (siehe `LICENSE`)
- Copyright: `Copyright (c) 2026 Hanno Jenkel`
- In der App unter `Einstellungen` gibt es den Link `Fremdlizenzen` für die Übersicht aller Python-Abhängigkeiten (inkl. transitiv) mit Lizenzinformationen.
- Optionaler Neuaufbau des Lizenz-Caches:
  ```bash
  source .venv/bin/activate
  python -c "from belegmanager.legal import get_third_party_notices; get_third_party_notices(force_refresh=True)"
  ```
