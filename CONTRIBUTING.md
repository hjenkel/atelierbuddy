# Contributing

Vielen Dank für dein Interesse an Atelier Buddy.

Diese Datei beschreibt den üblichen Ablauf für Beiträge an diesem Repository. Sie richtet sich an Menschen, die Fehler beheben, Dokumentation verbessern oder neue Funktionen ergänzen möchten.

## Ziel des Projekts

Atelier Buddy ist eine lokale Belegverwaltung für Künstler:innen, Bands und andere kreative Einzel- oder Kleinstteams.

Wichtige Leitlinien:
- einfache Erfassung und Zuordnung von Belegen
- kein Fokus auf USt-Voranmeldung oder ELSTER
- einfache Bedienung steht im Vordergrund 
- pragmatischer Lösungen statt Abbildung aller denkbaren Anwendungsfällen

## Voraussetzungen

- Python 3.12
- NiceGUI
- SQLite
- optional für OCR lokal: `ocrmypdf`, `tesseract`, `ghostscript`

## Lokales Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .[dev]
python -m belegmanager
```

App-URL lokal:

```text
http://127.0.0.1:8080
```

## Wichtige Kommandos

App starten:

```bash
python -m belegmanager
```

Tests ausführen:

```bash
./.venv/bin/python -m pytest -q
```

Docker lokal:

```bash
docker compose up --build -d
```

## Entwicklungsprinzipien

Bitte beachte beim Beitragen besonders diese Projektregeln:

- UI-Code gehört nach `belegmanager/ui/*`.
- Fachlogik gehört nach `belegmanager/services/*`.
- Das Datenmodell liegt in `belegmanager/models.py`.
- Datenbank-Setup und interne Migrationen liegen in `belegmanager/db.py`.
- Fachliche Zuordnungen haben genau eine Wahrheitsquelle: `cost_allocation`.
- Geldwerte werden als `*_cents` gespeichert, niemals als Float.
- Schemaänderungen dürfen keine bestehenden Nutzerdaten oder Archivdateien automatisch löschen.

## Do-not-Rules

Bitte vermeide insbesondere:

- Geschäftslogik in UI-Eventhandlern zu duplizieren
- eine zweite Wahrheitsquelle neben den Allokationen einzuführen
- Vorzeichenregeln bei Belegen zu verletzen
- Validierung von Eingaben zu umgehen
- lokale Nutzerdaten unter `data/` zu committen

Fachliche Vorzeichenregeln:
- `invoice` => Brutto `>= 0`
- `credit_note` => Brutto `<= 0`

## Wie Beiträge vorbereitet werden sollten

Ein guter Beitrag ist möglichst klar abgegrenzt:

- ein Bugfix
- eine kleine funktionale Erweiterung
- eine Doku-Anpassung
- eine gezielte technische Verbesserung

Wenn du Verhalten änderst, prüfe bitte auch:
- `README.md`
- `docs/developer/*`
- `CHANGELOG.md`

## Tests und manuelle Prüfung

Eine Änderung gilt in der Regel erst dann als bereit für Review, wenn:

1. die App lokal startet
2. die Tests grün sind
3. betroffene Kernflüsse manuell geprüft wurden
4. keine neuen Nutzerdaten versehentlich im Repo auftauchen
5. Doku bei Verhaltensänderungen aktualisiert wurde

## Versions- und Release-Hinweise

Die Version hat eine Single Source of Truth:

```text
pyproject.toml -> [project].version
```

## Pull-Request-Hinweise

Bitte beschreibe in einem Pull Request möglichst kurz:

- was geändert wurde
- warum die Änderung nötig ist
- welche Risiken oder Randfälle es gibt
- wie getestet wurde

Hilfreich sind außerdem:

- Screenshots bei UI-Änderungen
- Hinweise auf betroffene Datenflüsse oder Migrationen
- kurze Liste offener Punkte, falls bewusst etwas später folgt

## Dokumentation

Weiterführende technische Dokumentation:

- [README.md](./README.md)
- [docs/README.md](./docs/README.md)
- [docs/developer/README.md](./docs/developer/README.md)

## Fragen und Beiträge

Wenn du an einer größeren Änderung arbeitest, ist eine kleine Vorab-Abstimmung sinnvoll, bevor umfangreiche Umbauten gestartet werden.
