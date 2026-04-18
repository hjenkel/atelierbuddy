# Atelier Buddy

Lokale Web-App für Belegverwaltung, Ausgangsrechnungen und betriebliche Auswertungen in kreativen Arbeitskontexten.

Aktuelle Version: `0.3.0` (pre-alpha)

## Was ist Atelier Buddy?
Atelier Buddy richtet sich an Künstler:innen, Bands und andere Solo- oder Kleinstteams, die Belege, Kontakte, Projekte und Verkäufe an einem Ort pflegen möchten, ohne in klassische ERP- oder Steuerkanzlei-Software zu wechseln.

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
- Automatische Erzeugung einer Rechnungs-PDF
- Einnahmenauswertung mit Projektdrilldown
- Soft-Delete für Belege und Verkäufe, inklusive Wiederherstellung
- Validierung und Absicherung der Eingaben
- Auch für Mobilgeräte optimiertes UI

## Nutzung
- Die App ist für den Betrieb auf einem Homeserver (z. B. Raspberry Pi) gedacht, kann aber auch  und lokal installiert werden.
- Persistente Daten liegen unter `data/`.
- Die App ist in Entwicklung. Funktionen und UI können sich noch deutlich ändern.

## Wichtige Hinweise
- Die Software wird ohne Gewährleistung bereitgestellt und ist explizit keine GoBD.
- Nutzung erfolgt in eigener Verantwortung.
- Regelmäßige Backups sind dringend empfohlen.

### Vibe-Coding Transparenz
Diese App wurde zu einem sehr großen Teil mit KI-Coding-Assistenten erstellt. Ich bin daher für jeden echten Programmierer und Experten dankbar, der mal einen Blick auf den Code werfen kann. Hinweise oder Verbesserungen sind ausdrücklich erwünscht. Insbesondere im Bereich Sicherheit, Stabilität und Performance.

## Schnellstart lokal mit Docker
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

Diese Variante baut das Image lokal aus dem Checkout und ist für Entwicklung oder Einzelinstallationen gedacht.

### Ersteinrichtung
Beim ersten Start muss ein Admin-Account über `/setup` angelegt werden.

1. `/setup` im Browser öffnen.
2. Ersten Benutzer anlegen.

## Serverbetrieb mit Release-Image
Für einen Homeserver oder eine andere dauerhafte Installation kann statt eines lokalen Builds das veröffentlichte Image aus GitHub Container Registry verwendet werden.

Empfohlenes Compose-Beispiel:

```yaml
services:
  atelier-buddy:
    container_name: atelier-buddy
    image: ghcr.io/hjenkel/atelierbuddy:latest
    ports:
      - "12321:8080"
    environment:
      BM_HOST: "0.0.0.0"
      BM_SESSION_SECRET: "${BM_SESSION_SECRET:-}"
      PYTHONUNBUFFERED: "1"
    volumes:
      - atelier_buddy_data:/app/data
    restart: unless-stopped
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080', timeout=5).read()\"",
        ]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s

volumes:
  atelier_buddy_data:
```

Start oder Update auf dem Server:

```bash
docker compose pull
docker compose up -d
```

Wichtig:
- Persistente Daten liegen weiterhin im Volume unter `/app/data`.
- Datenbank und Archivdateien sind nicht Teil des Docker-Images.
- Für reproduzierbare Releases kann statt `latest` auch ein Versions-Tag wie `ghcr.io/hjenkel/atelierbuddy:0.3.0` verwendet werden.

## Lokale Installation
### Voraussetzungen
- Python 3.12+
- `ocrmypdf`, `tesseract`, `ghostscript` für Volltextsuche aus PDFs und Bildern
- für automatische Rechnungs-PDFs: `WeasyPrint` plus benötigte Systembibliotheken

macOS:
```bash
brew install ocrmypdf tesseract ghostscript
brew install tesseract-lang
brew install weasyprint
```

Ubuntu/Debian:
```bash
sudo apt update
sudo apt install -y ocrmypdf tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng ghostscript libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
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

Im Docker-Betrieb mit benanntem Volume sollte stattdessen das Volume bzw. der gemountete Datenpfad gesichert werden.

## Entwicklung
- Start lokal: `python -m belegmanager`
- Tests: `./.venv/bin/python -m pytest -q`
- Versionsquelle: `pyproject.toml` -> `[project].version`
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Rechnungssteller:in, Steuerkennzeichen, Bankverbindung, Zahlungsziel und Logo werden in der App unter `Einstellungen` gepflegt.

## Dokumentation
- Überblick: [docs/README.md](docs/README.md)
- Technische Doku: [docs/developer/README.md](docs/developer/README.md)

## Lizenz und Rechtliches
- Lizenz: `AGPL-3.0-or-later`
- Copyright: `Copyright (c) 2026 Hanno Jenkel`
- Fremdlizenzen sind in der App unter `Einstellungen` einsehbar.
