# Changelog

Alle wichtigen Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

Das Format orientiert sich an Keep a Changelog, und dieses Projekt folgt Semantic Versioning (`0.y.z` vor `1.0`).

## [0.3.5] - 2026-04-22
### Geändert
- Dashboard-Importdialog bereinigt: Der Datei-Upload lässt sich wieder zuverlässig direkt aus dem Dashboard öffnen und verwenden.
- Belegdetailansicht korrigiert: Manuell eingegebene Werte im Feld `Belegdatum` werden nun auch ohne Kalenderauswahl beim Speichern übernommen.

## [0.3.4] - 2026-04-22
### Hinzugefügt
- iOS-Homescreen-Icon auf Basis des Hamster-Brandings ergänzt.
- Pinch-to-Zoom für Bildvorschauen in der mobilen Belegdetailansicht ergänzt.

### Geändert
- Kostenkategorien aktualisieren nach dem Anlegen jetzt sofort ohne Seiten-Reload.
- Globalen Viewport und Auth-Seiten auf app-artiges Mobilverhalten mit deaktiviertem Browser-Zoom vereinheitlicht.
- Belegvorschau in einspaltigen Layouts kompakter gestaltet und Touch-Ziele der Viewer-Toolbar auf Mobilgeräten vergrößert.

## [0.3.3] - 2026-04-19
### Geändert
- Beleg-Speicherflow vereinheitlicht: Belege lassen sich nun auch bei noch unvollständigen Pflichtangaben speichern, ohne dass Zuordnungsentwürfe verloren gehen.
- Vollständigkeit von Belegen und Kostenzuordnungen wird jetzt zentral berechnet; nur vollständige Zuordnungen gelten als `posted` und fließen in fachliche Auswertungen ein.
- Detailansicht für Belege aktualisiert: Live-Vollständigkeitsanzeige, stabilerer Speichern-Handler und Rücknavigation zur Herkunftsseite nach dem Speichern.
- Mobile Unterstützung für Projekt-, Kategorie- und Anbieteransichten sowie verbesserte Aktionsmenüs ergänzt.
- Sidebar-Anzeige auf Mobilgeräten bei bestimmten Zuständen korrigiert.

## [0.3.2] - 2026-04-18
### Hinzugefügt
- Installweites Rechnungssteller-Profil mit Bankverbindung, Steuerkennzeichen, Zahlungsziel und Logo in den Einstellungen ergänzt.
- Automatische Rechnungs-PDF-Erzeugung fuer Verkäufe ueber ein festes HTML/CSS-Standardtemplate ergänzt.

### Geändert
- Verkaufsdetail um PDF-Erzeugung, Dokumentquelle und Sperrung rechnungsrelevanter Felder bei vorhandenem Rechnungsdokument erweitert.
- Datenmodell, Migrationen, Tests und Entwicklerdokumentation fuer automatische Rechnungsdokumente erweitert.

## [0.2.4] - 2026-04-18
### Geändert
- Mobilansichten für Navigation, Hauptlisten und zentrale Detail-/Erfassungsseiten überarbeitet.
- Mobile Tabellenansichten kompakter gestaltet und visuell klarer getrennt.
- Projektdetailansicht bereinigt: Ohne hinterlegtes Cover wird keine leere Bildvorschau mehr angezeigt.

## [0.2.3] - 2026-04-17
### Geändert
- Ersteinrichtung für Home-Nutzung vereinfacht: Der erste Admin kann direkt über `/setup` angelegt werden, ohne Setup-Token aus Logs oder Konsole.
- Setup-, Login- und Sicherheitsfluss für die vereinfachte Ersteinrichtung bereinigt und die zugehörigen Tests angepasst.
- Einstellungen auf die zentrale Versionsquelle umgestellt, damit die App-Version konsistent aus `pyproject.toml` kommt.
- Reihenfolge in Menü und Auswertung angepasst: Verkäufe stehen nun vor Belegen.
- Verkaufsdetail überarbeitet: Das Notizfeld ist wieder als festes, konsistentes Feld ohne manuelles Aufziehen ausgeführt.
- README sowie Entwicklerdokumentation für den vereinfachten Setup-Ablauf und die UI-Anpassungen aktualisiert.

## [0.2.2] - 2026-04-17
### Hinzugefügt
- Adressfelder und ein Standardland für Kontakte ergänzt.
- Länderliste und Zuordnungen für die Kontakterfassung ergänzt.
- Behandlung ungespeicherter Änderungen ergänzt, einschließlich Navigationsschutz und Statusverfolgung.

### Geändert
- Kontaktmodell und zugehörige Datenbankmigrationen für Adressdaten erweitert.
- Detailansichten und Abstände in der Oberfläche für die überarbeitete Kontaktbearbeitung angepasst.
- Dokumentation und Konfigurationsreferenzen auf Version `0.2.2` aktualisiert.

## [0.2.1] - 2026-04-15
### Hinzugefügt
- Upload-Unterstützung für Ausgangsrechnungsdokumente ergänzt.
- Möglichkeit zum Entfernen hochgeladener Rechnungsdokumente ergänzt.
- Persistente Ablage und Ersetzungslogik für Rechnungsdokumente erweitert.

### Geändert
- Verkaufslogik und zugehörige Tests für Rechnungsdokumente erweitert.
- Menüstruktur überarbeitet und das Styling der Aktionsschaltflächen verfeinert.
- CSS-Styling für Formularfelder und Projektumschalter für eine konsistentere Oberfläche verbessert.
- Dokumentation für Verkaufsdokumente, Datenmodell und Abläufe aktualisiert.

## [0.2] - 2026-04-14
### Hinzugefügt
- Verkaufsverwaltung (`Verkäufe`) mit eigener Service-Logik, Suche, Positionszeilen und Auswertungsanbindung ergänzt.
- Kontaktverwaltung ergänzt.
- Stammdatenverwaltung fachlich in Services gebündelt und deutlich ausgebaut.
- Notizen für Belege ergänzt.
- Preisfeld für Projekte sowie Validierung beim Löschen von Projekten ergänzt.
- Neue Belegverwaltungs- und Dashboard-Funktionen in der Oberfläche ergänzt.
- Authentifizierung, Ersteinrichtungs-Flow sowie Härtung für Sessions, HTTP, WebSocket und Uploads ergänzt.
- Unterstützung für passwortbasiertes Hashing mit Argon2 ergänzt.
- Serverseitige Validierung und Sicherheitsmiddleware zentralisiert.
- Zusätzliche Tests für Authentifizierung, Sicherheit, Belege, Stammdaten, Verkäufe und Auswertungen ergänzt.

### Geändert
- Dashboard-Statistiken und Darstellung der Belegkarten überarbeitet.
- Projekteintrag in die Navigationskonfiguration verschoben und Tabellen-Styling ergänzt.
- Styling für Schaltflächen, Eingabefelder, Badges und Branding in der UI verfeinert.
- Favicon, Logo und Markenbadge visuell überarbeitet.
- Datenmodell, Schema-Versionen und Entwicklerdokumentation für Kontakte, Projekte und Verkäufe erweitert.
- Wildcard-Hostkonfiguration für `BM_ALLOWED_HOSTS` erlaubt und zugleich eine Warnung für unsichere Setups ergänzt.
- Dokumentation für Installation, Docker-Nutzung und den größeren `0.2`-Release aktualisiert.

## [0.1.0] - 2026-04-06
### Hinzugefügt
- Erste öffentliche Pre-Alpha-Funktionsbasis für die lokale App Atelier Buddy mit Basisfunktion eines Belegmanagers.
