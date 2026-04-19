# Developer-Dokumentation

Diese Sektion beschreibt den technischen Ist-Zustand von Atelier Buddy auf Basis des aktuellen Codes.

Version-Single-Source: `pyproject.toml` (`[project].version`, aktuell `0.3.3`).

## Inhalte
- [Datenbankstruktur](./database.md)
- [Prozesse und Flows](./processes.md)
- [Berechnungen und Validierung](./calculations.md)
- [Architekturentscheidungen](./decisions.md)

## Wofür diese Doku da ist
- Architekturgrenzen sichtbar machen
- fachliche Regeln dokumentieren
- Datenmodell und Service-Verantwortung erläutern
- Änderungen im Produktverhalten nachvollziehbar halten

## Leitprinzip
- Die Doku beschreibt das implementierte Verhalten.
- Bei Konflikten hat der Code Vorrang.
- Änderungen an Kernflüssen sollten diese Doku mit aktualisieren.

## Docker-Releases
Der Docker-Release-Workflow veröffentlicht Images nach `ghcr.io/hjenkel/atelierbuddy`, aber nur für Git-Tags im Format `vX.Y.Z`.

Release-Ablauf:
1. Version in `pyproject.toml` erhöhen.
2. `CHANGELOG.md` aktualisieren.
3. Commit erstellen und Git-Tag `vX.Y.Z` setzen.
4. Tag pushen.
5. GitHub Actions veröffentlicht die Tags `X.Y.Z`, `latest` und `sha-<commit>` nach GHCR.
6. Server mit `docker compose pull && docker compose up -d` aktualisieren.

Sicherheitsnetz:
- Der Release-Workflow bricht ab, wenn Git-Tag und `pyproject.toml`-Version nicht übereinstimmen.
- CI prüft bei Pull Requests und Pushes nach `main` sowohl die Python-Tests als auch einen Docker-Build ohne Push.
