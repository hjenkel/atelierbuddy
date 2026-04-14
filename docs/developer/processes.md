# Prozesse und Flows

Quelle: `belegmanager/services/*`, `belegmanager/ui/pages.py`, `belegmanager/app_state.py`  
Version-Single-Source: `pyproject.toml` (`0.2`)

## Service-Architektur
`app_state.get_services()` baut einen gemeinsamen Service-Container mit:
- `AuthService`
- `ImportService`
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
1. Auth-Setup wird geprüft und bei Bedarf ein Setup-Token erzeugt.
2. Der OCR-Worker wird gestartet.
3. Datenbank und Default-Daten werden initialisiert.
4. Seiten und Sicherheitsmiddleware werden registriert.

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
   - `ReceiptService.update_metadata(...)` speichert Kopf- und Betragsdaten
   - `CostAllocationService.save_allocations(...)` ersetzt die Zuordnungen transaktional
3. Harte Regeln:
   - Typ und Vorzeichen müssen zusammenpassen
   - Zuordnungssumme muss exakt dem Bruttobetrag entsprechen
   - jede Zuordnungszeile braucht Kostenkategorie und Unterkategorie

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

Zusätzliche Schutzregel:
- Verkäufe mit `invoice_date` oder `invoice_number` dürfen weder archiviert noch gelöscht werden

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

Ausgabe:
- Gesamtsumme
- Summen je Projekt
- Drilldown auf einzelne Verkäufe
- unzugeordnete Positionen im Bucket `Ohne Projekt`

## Warum diese Aufteilung
- Services halten Fachlogik aus der UI heraus
- Hintergrundverarbeitung hält die Oberfläche reaktiv
- klare Zustandsübergänge und Schutzregeln machen Datenverhalten nachvollziehbar
