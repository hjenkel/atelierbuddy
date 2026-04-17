# Developer-Dokumentation

Diese Sektion beschreibt den technischen Ist-Zustand von Atelier Buddy auf Basis des aktuellen Codes.

Version-Single-Source: `pyproject.toml` (`[project].version`, aktuell `0.2.2`).

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
