# Berechnungen und Validierung

Quelle: `belegmanager/ui/pages.py`, `belegmanager/services/receipt_service.py`, `belegmanager/services/cost_allocation_service.py`  
Version-Single-Source: `pyproject.toml` (`0.1.0`)

## Geldwerte und Parsing
Geld wird als Integer-Cents gespeichert (`amount_*_cents`).

Parser-Verhalten (`_parse_money_to_cents`):
- akzeptiert typische Formate wie `100`, `100,00`, `100.00`, `1.234,56`, `1,234.56`.
- entfernt Waehrung/Leerzeichen.
- beruecksichtigt Vorzeichen (je nach Kontext erlaubt/gesperrt).
- rundet auf Cent mittels `ROUND_HALF_UP`.

Warum:
- integerbasierte Summen sind robust und reproduzierbar.
- UI-Eingabe bleibt nutzerfreundlich trotz verschiedener Schreibweisen.

## Netto-Berechnung
Formel (zentral):
- `net = gross / (1 + vat_rate/100)`
- Rundung auf ganze Cents mit `ROUND_HALF_UP`.

Implementierung:
- `ReceiptService._calculate_net_cents(...)`
- UI-Vorschau nutzt dieselbe Logik (`_compute_net_cents`).

Warum:
- mathematisch korrekte Ableitung aus Brutto+USt.
- identische Logik in UI und Persistenz vermeidet Inkonsistenzen.

## Belegtyp und Vorzeichen
`document_type`:
- `invoice` (Rechnung): Brutto muss `>= 0`
- `credit_note` (Gutschrift): Brutto muss `<= 0`

Validierung:
- in `ReceiptService.update_metadata(...)`
- erneut in `CostAllocationService._validate_allocations_payload(...)`

Warum:
- Vorzeichenkonsistenz ist zentral fuer Auswertung und Datenintegritaet.
- doppelte Absicherung verhindert fehlerhafte Zustaende.

## Kostenzuordnungen (Allokationen)
Pflichtregeln:
- mindestens eine Zeile,
- jede Zeile mit Kostenkategorie + Unterkategorie,
- Zeilenbetrag darf nicht `0` sein,
- Summe aller Zeilen == `receipt.amount_gross_cents`,
- Vorzeichen jeder Zeile muss zum Belegtyp passen.

Spezialfall ohne Projekt:
- `CostAllocationService.save_allocations(...)` setzt automatisch
  die technische Kostenstelle `Allgemeine Ausgabe`.

Warum:
- fachliche Wahrheit liegt in Zuordnungszeilen, nicht in parallelen Feldern.
- Summentreue ermoeglicht belastbare Reports.

## Vollstaendigkeit in der UI
Fehlende Pflichtangaben werden auf Basis von:
- Kopf-Feldern (`doc_date`, Anbieter, Brutto/USt/Netto, Typ),
- plus `CostAllocationService.validate_for_receipt(...)`
ermittelt.

Warum:
- Nutzer sieht sofort, welche Information noch fehlt, bevor Daten in Reports auftauchen.
