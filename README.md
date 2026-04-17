# Atelier Buddy

Lokale Web-App für Belegverwaltung, Ausgangsrechnungen und betriebliche Auswertungen in kreativen Arbeitskontexten.

Aktuelle Version: `0.2.2` (pre-alpha)

## Was ist Atelier Buddy?
Atelier Buddy richtet sich an Künstler:innen, Bands und andere Solo- oder Kleinstteams, die Belege, Kontakte, Projekte und Verkäufe an einem Ort pflegen möchten, ohne in klassische ERP- oder Steuerkanzlei-Software zu wechseln.

Die App ist heute mehr als ein "einfacher Belegsammler": Sie verbindet Dokumentarchiv, OCR, fachliche Zuordnung, Ausgangsrechnungslogik, Stammdatenpflege und projektbezogene Auswertung in einer lokalen Anwendung.

## Was kann Atelier Buddy?
- Kontakte pflegen. Mögliche Kudnen, Partner, Presse und Co. 
- Die eigenen Projekte (bspw. Kunstwerke) mit Erstelldatum, Foto und Preis verwalten
- Zahlungsbelege archivieren (als PDFs / Bilder) und per Volltextsuche durchsuchen
- Ausgaben zu Belegen fachlich über Kostenkategorien, Unterkategorien und Projekte zuordnen
- Verkäufe und mehreren Positionen verwalten
- Einnahmen und Ausgaben übersichtlich auswerten

## Weitere Funktionen
- Login-Schutz mit einfacher Ersteinrichtung
- Batch-Import von PDF-, JPG-, PNG- und HEIC-Dateien
- OCR-Texterkennung und Volltextsuche in Belegen
- Belegdetails mit Brutto/USt/Netto, Anbieter, Typ, Notizen
- Kostenzuordnung pro Beleg mit Split-Aufteilung
- Individuelle Verwaltung der Kontaktkategorien, Anbieter (Shops, Partner und Co) und Kostenkategorien.
- Kontakt-Datenbank
- Verkaufsverwaltung, welche Einnahmen darstellt und ausgehende Rechnungen archivieren kann.
- Rechnungsdokument je Verkauf mit direktem Upload/Ersetzen in der Detailansicht
- Einnahmenauswertung mit Projektdrilldown
- Soft-Delete für Belege und Verkäufe, inklusive Wiederherstellung
- Validierung und Absicherung der Eingaben

## Nutzung
- Die App ist für den Betrieb auf einem Homeserver (z. B. Raspberry Pi) gedacht, kann aber auch  und lokal installiert werden.
- Persistente Daten liegen unter `data/`.
- Die App ist in Entwicklung. Funktionen und UI können sich noch deutlich ändern.

## Wichtige Hinweise
- Die Software wird ohne Gewährleistung bereitgestellt und ist explizit keine GoBD.
- Nutzung erfolgt in eigener Verantwortung.
- Regelmäßige Backups sind dringend empfohlen.

## Schnellstart mit Docker
### Voraussetzungen
- Docker Desktop oder Docker Engine mit Compose Plugin

### Start
```bash
git clone https://github.com/hjenkel/atelierbuddy.git
cd atelierbuddy
echo "BM_SESSION_SECRET=$(openssl rand -hex 32)" > .env
docker compose up --build -d
```

App-URL: `http://localhost:12321`

### Ersteinrichtung
Beim ersten Start muss ein Admin-Account über `/setup` angelegt werden.

1. `/setup` im Browser öffnen.
2. Ersten Benutzer anlegen.

## Lokale Installation
### Voraussetzungen
- Python 3.12+
- `ocrmypdf`, `tesseract`, `ghostscript` im Systempfad

macOS:
```bash
brew install ocrmypdf tesseract ghostscript
brew install tesseract-lang
```

Ubuntu/Debian:
```bash
sudo apt update
sudo apt install -y ocrmypdf tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng ghostscript
```

### Start
```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
python -m belegmanager
```

App-URL lokal: `http://127.0.0.1:8080`

## Konfiguration
Wichtige ENV-Parameter:
- `BM_SESSION_SECRET`: Secret für signierte Session-Cookies
- `BM_ALLOWED_HOSTS`: Host-Allowlist, Default `*`
- `BM_ALLOWED_ORIGINS`: optionale Origin-Allowlist
- `BM_SESSION_IDLE_MINUTES`: Default `480`
- `BM_SESSION_MAX_AGE_HOURS`: Default `168`
- `BM_SECURE_COOKIES`: `auto`, `true` oder `false`
- `BM_MAX_UPLOAD_MB`: Default `25`
- `BM_OCR_TIMEOUT_SECONDS`: Default `300`
- `BM_OCR_LANGUAGES`: Default `deu+eng`

## Daten und Backups
Persistente Laufzeitdaten:
- `data/belegmanager.db`
- `data/archive/`

Einfaches Backup:
```bash
tar -czf atelierbuddy-backup-$(date +%Y-%m-%d).tar.gz data
```

## Entwicklung
- Start lokal: `python -m belegmanager`
- Tests: `./.venv/bin/python -m pytest -q`
- Versionsquelle: `pyproject.toml` -> `[project].version`
- Changelog: [CHANGELOG.md](CHANGELOG.md)

## Dokumentation
- Überblick: [docs/README.md](docs/README.md)
- Technische Doku: [docs/developer/README.md](docs/developer/README.md)

## Lizenz und Rechtliches
- Lizenz: `AGPL-3.0-or-later`
- Copyright: `Copyright (c) 2026 Hanno Jenkel`
- Fremdlizenzen sind in der App unter `Einstellungen` einsehbar.
