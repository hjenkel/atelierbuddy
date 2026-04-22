# Atelier Buddy

Lokale Web-App für Belegverwaltung, Ausgangsrechnungen und betriebliche Auswertungen in kreativen Arbeitskontexten.

Aktuelle Version: `0.3.5`

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
- Kostenzuordnung pro Beleg mit Split-Aufteilung sowie Entwurfs-/Vollständigkeitslogik
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

## Installation mit Docker
Für einen Homeserver solltest du ein Verzeichnis anlegen. Dort legst eine docker-compose.yml an, die den unten angegebenen Inhalt enthält.

Wichtig:
- `BM_SESSION_SECRET` muss vor dem ersten Start durch einen eigenen zufälligen Wert ersetzt werden.
- Einen geeigneten Wert kannst du z. B. mit `openssl rand -hex 32` erzeugen.
- Das Secret sollte bei Updates unverändert bleiben, damit bestehende Sessions nicht ungültig werden.

Empfohlenes Compose-Beispiel:

```yaml
services:
  atelier-buddy:
    container_name: atelier-buddy
    image: ghcr.io/hjenkel/atelierbuddy:latest
    ports:
      - "12321:8080"
    environment:
      BM_SESSION_SECRET: "HIER ZUFÄLLIGEN CODE EINGEBEN"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

Start oder Update auf dem Server:

```bash
cd /dein/Verzeichnis
docker compose pull
docker compose up -d
```

Hinweise:
- Die App ist danach standardmäßig unter `http://hostname:12321` erreichbar.
- Persistente Daten liegen im gemounteten Host-Verzeichnis `./data`, das im Container unter `/app/data` eingebunden ist.
- Falls Docker beim ersten Start einen Berechtigungsfehler für `/app/data` meldet, hilft auf Linux meist: `sudo chown -R 10001:10001 data`
- Für reproduzierbare Releases kann statt `latest` auch ein Versions-Tag wie `ghcr.io/hjenkel/atelierbuddy:0.3.5` verwendet werden.

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


### Ersteinrichtung
Beim ersten Start wird ein Admin-Account angelegt.

## Entwicklung
- Start lokal: `python -m belegmanager`
- Tests: `./.venv/bin/python -m pytest -q`
- Versionsquelle: `pyproject.toml` -> `[project].version`
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Rechnungssteller:in, Steuerkennzeichen, Bankverbindung, Zahlungsziel und Logo werden in der App unter `Einstellungen` gepflegt.

## Dokumentation
- Überblick: [docs/README.md](docs/README.md)
- Installation & Betrieb: [docs/installation.md](docs/installation.md)
- Technische Doku: [docs/developer/README.md](docs/developer/README.md)

## Hinweise
- Die Software wird ohne Gewährleistung bereitgestellt und ist explizit keine GoBD.
- Nutzung erfolgt in eigener Verantwortung.
- Regelmäßige Backups sind dringend empfohlen.

### Vibe-Coding Transparenz
Diese App wurde zu einem sehr großen Teil mit KI-Coding-Assistenten erstellt. Ich bin daher für jeden echten Programmierer und Experten dankbar, der mal einen Blick auf den Code werfen kann. Hinweise oder Verbesserungen sind ausdrücklich erwünscht. Insbesondere im Bereich Sicherheit, Stabilität und Performance.

### Lizenz und Rechtliches
- Lizenz: `AGPL-3.0-or-later`
- Copyright: `Copyright (c) 2026 Hanno Jenkel`
- Fremdlizenzen sind in der App unter `Einstellungen` einsehbar.
