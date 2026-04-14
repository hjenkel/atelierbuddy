# Prozesse und Flows

Quelle: `belegmanager/services/*`, `belegmanager/ui/pages.py`, `belegmanager/app_state.py`  
Version-Single-Source: `pyproject.toml` (`0.1.0`)

## Service-Architektur
`app_state.get_services()` erstellt einen Singleton-Container:
- `ImportService`
- `OCRService`
- `OCRJobQueue` (Background-Worker-Thread)
- `SearchService`
- `OrderSearchService`
- `ReportService`
- `CostAllocationService`
- `ReceiptService`
- `OrderService`

## Docker-Laufzeit (empfohlen)
Standardbetrieb über `docker-compose.yml`:
- Container lauscht intern auf `8080`.
- Host-Port ist standardmäßig `12321`.
- Persistenz über Named Volume `atelier_buddy_data` auf `/app/data`.

Relevante ENV-Overrides:
- `BM_HOST` (Default lokal: `127.0.0.1`, in Docker: `0.0.0.0`)
- `BM_PORT` (Default: `8080`)
- `BM_OCR_LANGUAGES` (Default: `deu+eng`)

## Flow 1: Import -> OCR
1. UI startet Import (Dateien/Ordner).
2. `ImportService` erzeugt `ImportBatch` + `Receipt`-Einträge (`status=queued`).
3. Neue `receipt_id` werden in `OCRJobQueue.enqueue()` gestellt.
4. Worker ruft `OCRService.process_receipt(receipt_id)` auf.
5. OCR-Resultat:
   - OCR-PDF/Sidecar/Text/Thumbnail werden erzeugt (oder Fehlerstatus gesetzt),
   - FTS wird via `upsert_fts_row` aktualisiert,
   - optionales Datums-Vorschlagen aus Dokumenttext.

## Flow 2: Beleg bearbeiten/speichern
1. Belegdetailseite lädt Stammdaten + bestehende Zuordnungen.
2. Beim Speichern:
   - `ReceiptService.update_metadata(...)` speichert Kopf-, Notiz- und Betragsdaten.
   - `CostAllocationService.save_allocations(...)` ersetzt die Zuordnungszeilen transaktional.
3. Harte Regeln:
   - Belegtyp/Vorzeichen konsistent (`invoice` >= 0, `credit_note` <= 0).
   - Zuordnungssumme muss exakt Brutto entsprechen.
   - Jede Zuordnungszeile braucht Kostenkategorie + Unterkategorie.

## Flow 2b: Verkauf bearbeiten/speichern
1. Verkauf wird zunächst mit Kontakt + Verkaufsdatum angelegt; die interne Verkaufsnummer wird sofort vergeben.
2. Auf der Detailseite speichert `OrderService.save_order(...)` Kopf- und Positionsdaten transaktional.
3. Harte Regeln:
   - `sale_date` ist Pflicht.
   - `invoice_date` => `invoice_number` Pflicht und eindeutig.
   - sobald `invoice_date` oder `invoice_number` gesetzt ist, kann der Verkauf nicht mehr gelöscht oder archiviert werden.
   - mindestens eine Position,
   - jede Position braucht Beschreibung, Menge `> 0` und Einzelpreis; Projekt ist optional.

## Flow 3: Suche und Filter
`SearchService.search(...)` kombiniert:
- FTS-Query (`receipt_fts`)
- Filter auf Datum, Anbieter, Projekt, Kostenkategorie, Unterkategorie, Kostenstelle
- Soft-Delete-Sicht (`include_deleted`, `deleted_only`)

Ergebnis wird mit `selectinload(...)` geladen, damit UI-Ansichten ohne N+1-Probleme rendern.

## Flow 4: Löschen
- **Soft delete**: `ReceiptService.move_to_trash` setzt `deleted_at`.
- **Restore**: `restore_from_trash` setzt `deleted_at = NULL`.
- **Hard delete**:
  - löscht `cost_allocation` + FTS-Zeile + `receipt`,
  - entfernt archivierte Dateien (Original/OCR/Thumbnail/Normalisierung).

## Flow 5: Auswertung
`ReportService` nutzt nur **auswertbare** Belege:
- nicht gelöscht,
- `doc_date` gesetzt,
- `amount_gross_cents` gesetzt,
- mindestens eine Zuordnung,
- Summe der Zuordnungen == Beleg-Brutto.

Ausgabe:
- Gesamtsumme im Zeitraum,
- Summen je Kostenkategorie,
- Drilldown auf Unterkategorien.

Zusätzlich für Einnahmen:
- nur abgerechnete Verkäufe (`invoice_date` gesetzt, nicht gelöscht, mindestens eine Position),
- Aggregation nach `invoice_date`,
- Summen je Projekt,
- Drilldown auf abgerechnete Verkäufe innerhalb des gewählten Projekts.

## Warum diese Prozessaufteilung
- Services halten Fachlogik aus der UI heraus.
- Hintergrund-OCR entkoppelt lange Laufzeiten von der Bedienung.
- Klare Zustandsübergänge (`queued/running/done/error`) machen Fehler und Fortschritt nachvollziehbar.
