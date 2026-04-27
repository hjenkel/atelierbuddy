# Prozesse und Flows

Quelle: `belegmanager/services/*`, `belegmanager/ui/pages.py`, `belegmanager/app_state.py`, `belegmanager/receipt_completion.py`  
Version-Single-Source: `pyproject.toml` (`0.3.6`)

## Service-Architektur
`app_state.get_services()` baut einen gemeinsamen Service-Container mit:
- `AuthService`
- `ImportService`
- `InvoiceService`
- `OCRService`
- `OCRJobQueue`
- `SearchService`
- `OrderSearchService`
- `ReportService`
- `CostAllocationService`
- `ReceiptService`
- `OrderService`
- `MasterDataService`

Die UI soll Fachlogik nicht duplizieren, sondern diese Services verwenden.

## Laufzeit und Initialisierung
Beim App-Start:
1. Auth-Setup wird geprüft; solange noch kein Benutzer existiert, ist `/setup` für die Ersteinrichtung erreichbar.
2. Der OCR-Worker wird gestartet.
3. Datenbank und Default-Daten werden initialisiert.
4. Seiten und Sicherheitsmiddleware werden registriert.

Zusätzliche UI-/Mobil-Konventionen:
- die globale Viewport-Konfiguration sperrt Browser-Seitenzoom bewusst app-artig
- Login-/Setup-Seiten übernehmen dieselbe Viewport-Regel wie die Haupt-App
- die Bildvorschau in der Belegdetailseite bietet stattdessen gezieltes Zwei-Finger-Pinch-to-Zoom innerhalb des Viewer-Bereichs
- PDF-Vorschauen bleiben bei expliziten Zoom-Buttons; in einspaltigen Layouts ist die Vorschauhöhe auf ungefähr ein Drittel der Bildschirmhöhe begrenzt

## Flow 1: Import bis OCR
1. Nutzer startet einen Datei- oder Ordnerimport.
2. `ImportService` legt `ImportBatch` und `Receipt`-Datensätze an.
3. Neue Beleg-IDs werden an `OCRJobQueue.enqueue()` übergeben.
4. Der Worker ruft `OCRService.process_receipt(receipt_id)` auf.
5. Ergebnis:
   - OCR-PDF, OCR-Text und Thumbnail werden erzeugt oder Fehlerstatus gesetzt
   - FTS wird aktualisiert
   - Dokumentdatum kann heuristisch vorgeschlagen werden

## Flow 2: Beleg bearbeiten und speichern
1. Die Belegdetailseite lädt Beleg, Stammdaten und bestehende Zuordnungen.
2. Beim Speichern:
   - `ReceiptService.save_detail(...)` speichert Kopf-, Betrags- und Zuordnungsdaten in einem gemeinsamen Flow
   - `ReceiptCompletionService` bewertet Vollständigkeit und fehlende Pflichtangaben zentral
   - `CostAllocationService.replace_allocations(...)` persistiert die Zuordnungen mit Status `draft` oder `posted`
3. Fachliche Regeln:
   - Belegdaten dürfen auch unvollständig gespeichert werden
   - nur vollständig bewertete Belege publizieren ihre Zuordnungen als `posted`
   - unvollständige Zuordnungen bleiben als `draft` erhalten und gehen beim Speichern nicht verloren
4. Harte Regeln für `posted`:
   - Typ und Vorzeichen müssen zusammenpassen
   - Zuordnungssumme muss exakt dem Bruttobetrag entsprechen
   - jede veröffentlichte Zuordnungszeile braucht Kostenkategorie und Unterkategorie

## Flow 3: Verkauf anlegen und speichern
1. Ein neuer Verkauf wird mit Kontakt und Verkaufsdatum angelegt.
2. `OrderService.create_order(...)` vergibt sofort die interne Nummer im Format `YYYY-0001`.
3. In der Detailseite werden Kopf- und Positionsdaten bearbeitet.
4. `OrderService.save_order(...)` speichert den Verkauf transaktional.

Harte Regeln:
- `sale_date` ist Pflicht
- `invoice_date` verlangt eine eindeutige `invoice_number`
- mindestens eine Position ist erforderlich
- jede Position braucht Bezeichnung, Menge und Einzelpreis
- Projekt pro Position ist optional

Rechnungsdokument:
- Upload/Ersetzen passiert direkt auf der Verkaufsdetailseite
- genau eine Datei pro Verkauf
- erlaubte Dateitypen entsprechen Belegen
- kein OCR, kein ImportBatch, kein Thumbnail
- Dokumentquelle wird als `generated` oder `uploaded` gespeichert
- automatische PDF-Erzeugung läuft über `InvoiceService`
- vor dem Generieren werden aktive Formularänderungen in der UI zunächst gespeichert
- fehlendes Rechnungsdatum wird beim Generieren automatisch auf `heute` gesetzt
- die Rechnungsnummer wird beim Generieren im Format `RE-<Verkaufsnummer>` gesetzt
- PDF-Erzeugung rendert die installweit gewählte Standard- oder eigene HTML/CSS-Vorlage über `WeasyPrint`
- die erzeugte PDF wird als Snapshot gespeichert und später nicht still neu generiert
- sobald ein Rechnungsdokument vorhanden ist, sind Kontakt, Positionen, Verkaufsdatum, Rechnungsdatum und Rechnungsnummer gesperrt
- `Abgerechnet` ist erst erreicht, wenn Rechnungsdatum, Rechnungsnummer und Datei vorhanden sind

## Flow 4: Suchen und Filtern
### Belege
`SearchService.search(...)` kombiniert:
- FTS-Query
- Datums-, Anbieter-, Projekt- und Kategoriefilter
- Soft-Delete-Sicht

### Verkäufe
`OrderSearchService.search(...)` kombiniert:
- Suche über Verkaufsnummer, Rechnungsnummer, Kontakt und Notiz
- Filter auf Kontakt, Projekt, Status und Datumsbereich
- aktive und gelöschte Verkäufe

## Flow 5: Löschen und Wiederherstellen
### Belege
- Soft-Delete über `ReceiptService.move_to_trash(...)`
- Wiederherstellung über `restore_from_trash(...)`
- Hard-Delete entfernt Datensatz, Zuordnungen, FTS und Archivdateien

### Verkäufe
- Soft-Delete über `OrderService.move_to_trash(...)`
- Wiederherstellung über `restore_from_trash(...)`
- Hard-Delete entfernt Verkauf und Positionen
- vorhandene Rechnungsdateien können auf der Detailseite entfernt werden; danach werden die rechnungsrelevanten Felder wieder editierbar

Zusätzliche Schutzregel:
- Verkäufe mit `invoice_date`, `invoice_number` oder Rechnungsdokument dürfen weder archiviert noch gelöscht werden

## Flow 6: Stammdatenpflege
`MasterDataService` verwaltet:
- Projekte
- Kontakte
- Kontaktkategorien
- Anbieter
- Kostenkategorien
- Unterkategorien

Wichtige Schutzregeln:
- Kontakte dürfen nicht gelöscht werden, wenn Verkäufe referenzieren
- Projekte dürfen nicht gelöscht werden, wenn Beleg-Zuordnungen oder Verkaufspositionen referenzieren

Projekt-Detailverhalten seit `0.3.6`:
- die Detailseite nutzt dieselbe Toolbar-Konvention wie die Verkaufsdetails
- Speichern navigiert nach erfolgreicher Rückmeldung zurück zur Projektübersicht
- Projekte unterstützen eine optionale Notiz, die über `MasterDataService` validiert und gespeichert wird

## Flow 6a: Rechnungssteller pflegen
1. In `Einstellungen` wird das installweite Rechnungssteller-Profil bearbeitet.
2. `InvoiceService.update_profile(...)` validiert und speichert Absenderdaten, Steuerkennzeichen, Bankverbindung und Standard-Zahlungsziel.
3. Das Logo wird separat hochgeladen und als Archivpfad im Profil referenziert.

Wichtig:
- es gibt genau ein Rechnungssteller-Profil pro Installation
- die Profildaten wirken auf neu erzeugte Rechnungs-PDFs, nicht rückwirkend auf bestehende Snapshots

## Flow 7: Auswertung
### Ausgaben
`ReportService.build_summary(...)` berücksichtigt nur auswertbare Belege:
- nicht gelöscht
- `doc_date` gesetzt
- Bruttobetrag gesetzt
- mindestens eine Zuordnung
- Summe der Zuordnungen entspricht dem Bruttobetrag

Ausgabe:
- Gesamtsumme
- Summen je Kostenkategorie
- Drilldown auf Unterkategorien

### Einnahmen
`ReportService.build_income_summary(...)` berücksichtigt nur auswertbare Verkäufe:
- nicht gelöscht
- `invoice_date` gesetzt
- mindestens eine Position
- Dokumentstatus ist für die Auswertung nicht ausschlaggebend

Ausgabe:
- Gesamtsumme
- Summen je Projekt
- Drilldown auf einzelne Verkäufe
- unzugeordnete Positionen im Bucket `Ohne Projekt`

## Warum diese Aufteilung
- Services halten Fachlogik aus der UI heraus
- Hintergrundverarbeitung hält die Oberfläche reaktiv
- klare Zustandsübergänge und Schutzregeln machen Datenverhalten nachvollziehbar
