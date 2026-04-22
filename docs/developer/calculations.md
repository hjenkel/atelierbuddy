# Berechnungen und Validierung

Quelle: `belegmanager/ui/pages.py`, `belegmanager/services/receipt_service.py`, `belegmanager/services/cost_allocation_service.py`, `belegmanager/services/order_service.py`, `belegmanager/services/report_service.py`  
Version-Single-Source: `pyproject.toml` (`0.3.5`)

## Geldwerte und Parsing
Geld wird als Integer-Cents gespeichert.

Parser-Verhalten:
- akzeptiert typische Eingaben wie `100`, `100,00`, `100.00`, `1.234,56`, `1,234.56`
- entfernt Währungssymbole und Leerzeichen
- verarbeitet je nach Kontext positive und negative Werte
- rundet auf Cent mit `ROUND_HALF_UP`

Warum:
- Integer-Cents vermeiden Float-Fehler
- die UI bleibt tolerant gegenüber typischen Eingabeformaten

## Netto-Berechnung für Belege
Zentrale Formel:
- `net = gross / (1 + vat_rate/100)`

Implementierung:
- `ReceiptService._compute_net_cents(...)`

Regeln:
- Rundung auf ganze Cents mit `ROUND_HALF_UP`
- wenn Brutto leer ist, werden Brutto/USt/Netto gemeinsam geleert
- wenn kein USt-Satz gesetzt ist, bleibt Netto im Persistenzpfad leer

## Belegtyp und Vorzeichen
`document_type`:
- `invoice`: Brutto muss `>= 0` sein
- `credit_note`: Brutto muss `<= 0` sein

Validierung:
- in `ReceiptService.update_metadata(...)`
- zusätzlich in `CostAllocationService` für die Zuordnungslogik

Warum:
- Vorzeichenkonsistenz ist zentral für korrekte Auswertungen und robuste Datenintegrität

## Kostenzuordnungen
Die Vollständigkeit wird zentral über `ReceiptCompletionService` bewertet.

Regeln für gespeicherte Entwürfe (`draft`):
- Zuordnungszeilen dürfen unvollständig sein
- `cost_type_id` darf leer bleiben
- Betrag `0` ist nur für unvollständige Entwürfe tolerierbar

Regeln für veröffentlichte Zuordnungen (`posted`):
- mindestens eine Zuordnungszeile
- jede Zeile braucht Kostenkategorie und Unterkategorie
- der Zeilenbetrag darf nicht `0` sein
- Summe aller Zeilen muss exakt `receipt.amount_gross_cents` entsprechen
- das Vorzeichen der Zuordnungszeilen muss zum Belegtyp passen

Spezialfall ohne Projekt:
- wenn kein Projekt gesetzt ist, wird technisch die Kostenstelle `Allgemeine Ausgabe` verwendet

Warum:
- `cost_allocation` ist die fachliche Wahrheit für Ausgaben
- nur `posted`-Zeilen gehen in Reports und fachliche Sperrprüfungen ein

## Verkäufe und Positionssummen
Verkäufe speichern Geldbeträge als Integer-Cents und Mengen als `Decimal(12,3)`.

Regeln:
- jede Position braucht Bezeichnung
- Menge muss `> 0` sein
- maximal 3 Nachkommastellen für Mengen
- `unit_price_cents` kann positiv oder negativ sein
- Projekt ist optional

Berechnung:
- `order_item_total_cents = quantity * unit_price_cents`
- Rundung auf ganze Cents mit `ROUND_HALF_UP`
- die Verkaufssumme wird nicht separat persistiert, sondern aus den Positionen aggregiert

## Verkaufsnummern und Rechnungsnummern
Interne Verkaufsnummer:
- wird beim Anlegen sofort vergeben
- Format `YYYY-0001`
- Sequenz je Verkaufsjahr
- bleibt auch dann unverändert, wenn später das Verkaufsdatum geändert wird

Rechnungsnummer:
- darf leer sein
- wird beim Speichern getrimmt
- ist verpflichtend, sobald `invoice_date` gesetzt ist
- muss eindeutig sein
- automatische Vergabe beim PDF-Generieren läuft im Format `RE-YYYY-0001`
- die Rechnungssequenz ist unabhängig von der internen Verkaufsnummer
- wenn beim PDF-Generieren noch kein Rechnungsdatum gesetzt ist, wird zuerst `heute` als effektives Rechnungsdatum verwendet und daraus das Rechnungsjahr abgeleitet

## Statuslogik für Verkäufe
Der Status wird nicht gespeichert, sondern aus Feldern abgeleitet:
- `Entwurf`: keine relevanten Rechnungsdaten oder noch kein vollständiger Rechnungsstand
- `Dokument fehlt`: Rechnungsdatum oder Rechnungsnummer vorhanden, aber keine Rechnungsdatei
- `Abgerechnet`: Rechnungsdatum, Rechnungsnummer und Rechnungsdatei vorhanden

Zusätzliche Schutzregel:
- sobald `invoice_date`, `invoice_number` oder Rechnungsdatei gesetzt ist, kann der Verkauf nicht mehr gelöscht oder archiviert werden
- sobald eine Rechnungsdatei vorhanden ist, sind rechnungsrelevante Felder nur nach Entfernen des Dokuments wieder änderbar

## Rechnungserzeugung und Snapshot-Regel
- automatische Rechnungserzeugung validiert den gespeicherten Verkaufsstand zusammen mit dem installweiten `invoice_profile`
- vor dem Start der PDF-Erzeugung speichert die UI ungesicherte Formularänderungen automatisch
- gerendert wird ein festes HTML/CSS-Standardtemplate
- das resultierende PDF wird als Snapshot gespeichert
- Änderungen an Kontakt, Positionen, Rechnungsstellerdaten oder Logo wirken erst auf die nächste neu erzeugte Rechnung

## Report-Logik
Ausgabenreport:
- berücksichtigt nur aktive Belege
- `doc_date` muss gesetzt sein
- `amount_gross_cents` muss gesetzt sein
- mindestens eine Zuordnung
- Zuordnungssumme muss dem Bruttobetrag entsprechen

Einnahmenreport:
- berücksichtigt nur aktive Verkäufe mit `invoice_date`
- mindestens eine Position ist erforderlich
- Aggregation läuft nach `invoice_date`
- die Auswertung ist bewusst unabhängig vom Dokumentstatus
- Drilldown erfolgt über Projekte
- Positionen ohne Projekt laufen in den Bucket `Ohne Projekt`

## Vollständigkeit in der UI
Die UI markiert fehlende Pflichtangaben auf Basis von:
- Kopf-Feldern des Belegs
- Validierung der Kostenzuordnungen

Warum:
- unvollständige Belege werden früh sichtbar
- auswertbare Datensätze lassen sich besser von Zwischenständen unterscheiden
