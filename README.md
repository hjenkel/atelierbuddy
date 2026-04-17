# Atelier Buddy

Lokale Web-App für Belegverwaltung, Ausgangsrechnungen und betriebliche Auswertungen in kreativen Arbeitskontexten.

Aktuelle Version: `0.2.2` (pre-alpha)

## Was ist Atelier Buddy?
Atelier Buddy richtet sich an Künstler:innen, Bands und andere Solo- oder Kleinstteams, die Belege, Kontakte, Projekte und Verkäufe an einem Ort pflegen möchten, ohne in klassische ERP- oder Steuerkanzlei-Software zu wechseln.

Die App ist heute mehr als ein "einfacher Belegsammler": Sie verbindet Dokumentarchiv, OCR, fachliche Zuordnung, Ausgangsrechnungslogik, Stammdatenpflege und projektbezogene Auswertung in einer lokalen Anwendung.

## Aktueller Produktfokus
- Belege importieren, archivieren, per OCR erschließen und durchsuchbar machen
- Ausgaben fachlich über Kostenkategorien, Unterkategorien und optional Projekte zuordnen
- Verkäufe bzw. Ausgangsrechnungen mit Kontaktbezug und Positionszeilen verwalten
- Einnahmen und Ausgaben für eine EÜR-nahe Sicht auswerten
- lokalen, selbst betriebenen Einsatz statt Cloud- oder Team-ERP

## Bewusste Grenzen
- keine ELSTER-Integration
- keine USt-Voranmeldung
- keine Zahlungslogik für Einnahmen/Ausgaben mit eigener Buchungstabelle
- keine PDF-Rechnungserzeugung aus Verkäufen
- keine revisionssichere Buchhaltungs- oder Steuerlösung

## Kernfunktionen
- Login-Schutz mit einfacher Ersteinrichtung für den ersten Admin-Zugang
- Batch-Import von PDF-, JPG-, PNG- und HEIC-Dateien
- OCR mit `ocrmypdf` und Volltextsuche via SQLite FTS5
- Belegdetail mit Brutto/USt/Netto, Anbieter, Typ, Notizen und Vollständigkeitsstatus
- Kostenzuordnung pro Beleg mit Standardmodus oder Split-Aufteilung
- Stammdaten für Projekte, Kontakte, Kontaktkategorien, Anbieter und Kostenkategorien
- Kontakte mit Adressbereich für rechnungsrelevante Stammdaten
- Verkaufsverwaltung mit internem Nummernkreis, manueller Rechnungsnummer und Positionszeilen
- Rechnungsdokument je Verkauf mit direktem Upload/Ersetzen in der Detailansicht
- projektbezogene Verkaufspositionen mit Dezimalmengen und berechneten Zeilensummen
- Einnahmenauswertung nach Rechnungsdatum und Projektdrilldown
- Soft-Delete für Belege und Verkäufe, inklusive Wiederherstellung
- Fremdlizenz-Ansicht und Laufzeitinformationen in den Einstellungen

## Wichtige Betriebsrealität
- Die App ist lokal-first und self-hosted gedacht.
- Persistente Daten liegen unter `data/`.
- Bei einem Wechsel von `SCHEMA_VERSION` in `belegmanager/db.py` erfolgt derzeit ein Hard Reset von Datenbank und Archiv.
- Die App ist pre-alpha. Datenmodell, Workflows und UI können sich noch deutlich ändern.

## Wichtige Hinweise
- Die Software wird ohne Gewährleistung bereitgestellt.
- Es gibt keine steuerliche oder rechtliche Freigabe durch eine Kanzlei.
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
