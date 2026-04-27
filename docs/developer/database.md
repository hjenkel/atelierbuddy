# Datenbankstruktur

Quelle: `belegmanager/models.py`, `belegmanager/db.py`, `belegmanager/fts.py`  
Version-Single-Source: `pyproject.toml` (`0.3.6`)

## Überblick
Atelier Buddy nutzt SQLite mit SQLModel/SQLAlchemy. Das Datenmodell ist auf lokale Nutzung, überschaubare Betriebsgröße und schnelle Iteration ausgelegt.

Zentrale Bereiche:
- `receipt` für importierte oder manuell gepflegte Belege
- `cost_allocation` als fachliche Zuordnungs-Wahrheit für Ausgaben
- `sales_order` und `sales_order_item` für Verkäufe bzw. Ausgangsrechnungen
- Stammdaten für Projekte, Kontakte, Kontaktkategorien, Anbieter und Kategorien
- `receipt_fts` für lokale Volltextsuche
- `app_user` und `auth_attempt` für Anmeldung und Lockout-Basis

## ER-Übersicht
```mermaid
erDiagram
    RECEIPT ||--o{ COST_ALLOCATION : has
    COST_TYPE ||--o{ COST_SUBCATEGORY : has
    CONTACT_CATEGORY ||--o{ CONTACT : groups
    COST_TYPE ||--o{ COST_ALLOCATION : classifies
    COST_SUBCATEGORY ||--o{ COST_ALLOCATION : refines
    PROJECT ||--o{ COST_ALLOCATION : targets
    CONTACT ||--o{ SALES_ORDER : references
    SALES_ORDER ||--o{ SALES_ORDER_ITEM : has
    PROJECT ||--o{ SALES_ORDER_ITEM : targets
    COST_AREA ||--o{ COST_ALLOCATION : fallback_target
    SUPPLIER ||--o{ RECEIPT : issued_by
    IMPORT_BATCH ||--o{ RECEIPT : imported
    APP_USER ||--o{ AUTH_ATTEMPT : has
```

## Zentrale Tabellen
### `receipt`
Belegkopf mit Dokumentpfaden, OCR-Text, Belegdatum, Bruttobetrag, USt, Netto, Typ, Status und Soft-Delete.

### `cost_allocation`
Zuordnungszeilen für Ausgaben. Diese Tabelle ist die fachliche Wahrheit für Kostenkategorie, Unterkategorie, optionales Projekt, optionale technische Kostenstelle und Betrag.

Seit `0.3.4` trägt jede Zuordnungszeile zusätzlich einen Wirksamkeitsstatus:
- `draft` für gespeicherte, aber noch unvollständige oder fachlich nicht vollständig publizierbare Entwürfe
- `posted` für vollständige, offiziell wirksame Ausgaben

`cost_type_id` ist dafür nullable, damit auch unvollständige Entwurfszeilen ohne Dummy-Werte persistiert werden können.

### `sales_order`
Verkaufskopf mit:
- interner Verkaufsnummer
- Pflicht-Kontakt
- Verkaufsdatum
- optionalem Rechnungsdatum
- optionaler, eindeutiger Rechnungsnummer
- optionalem Rechnungsdokument mit Originaldateiname, Legacy-Upload-Zeitpunkt, neutralem Dokument-Zeitpunkt und Dokumentquelle (`generated` / `uploaded`)
- Notiz
- Soft-Delete

In v0.2 gibt es kein separates Rechnungsobjekt. Verkauf und Ausgangsrechnung sind derselbe Datensatz; die Rechnungsdatei hängt direkt am Verkauf.
Auch in `0.3.6` bleibt dieses Modell bewusst bestehen: automatische PDF-Erzeugung und manueller Upload arbeiten direkt auf demselben Dokument-Slot des Verkaufs.

### `invoice_profile`
Installweites Singleton für automatisch erzeugte Rechnungen mit:
- Absender-/Adressdaten
- Steuerkennzeichen-Typ und -Wert
- Bankverbindung
- Standard-Zahlungsziel
- optionalem Logo-Pfad

Die Werte werden in der UI unter `Einstellungen` gepflegt und vom `InvoiceService` für die automatische PDF-Erzeugung verwendet.

### `sales_order_item`
Positionszeilen eines Verkaufs mit:
- laufender Position
- Bezeichnung
- Menge als `Decimal(12,3)`
- `unit_price_cents`
- optionalem Projekt

Die Verkaufssumme wird nicht separat gespeichert, sondern aus diesen Positionen berechnet.

### Stammdaten
- `project`: Projekte inkl. Farbe, optionalem Preis, optionalem Cover-Bild und optionaler Notiz
- `contact`: personenzentrierte Kontakte mit Pflichtregel "Vorname oder Nachname" sowie Adressfeldern für Straße, Hausnummer, Adresszusatz, PLZ, Ort und Land; neue Kontakte starten mit Land `DE`
- `contact_category`: frei pflegbare Kontaktkategorien
- `supplier`: Anbieter für Belege
- `cost_type` und `cost_subcategory`: fachliche Kostenstruktur
- `cost_area`: technische Kostenstellenstruktur, derzeit UI-seitig weitgehend verborgen

### Dateiablage
- Belege liegen unter `data/archive/originals`.
- Verkaufs-Rechnungsdokumente liegen unter `data/archive/order_invoices`.
- Rechnungslogos liegen unter `data/archive/invoice_assets`.
- Eigene Rechnungsvorlagen liegen unter `data/invoice_templates/custom`.
- `/files/...` liefert beide Dateitypen aus dem Archiv aus.

Für generierte Rechnungen gilt:
- das PDF ist ein Snapshot des damaligen Verkaufs- und Rechnungsstellerstands
- `invoice_profile.invoice_template_mode` wählt installweit zwischen Standard- und eigener Vorlage
- spätere Änderungen an Kontakt, Positionen oder Rechnungsstellerdaten aktualisieren vorhandene PDFs nicht automatisch

### Auth-Tabellen
- `app_user`: lokale Benutzerkonten
- `auth_attempt`: Login-Versuche für Monitoring und Lockout

## Wichtige Beziehungen und Schutzregeln
- Kontakte können nicht gelöscht werden, solange Verkäufe auf sie referenzieren.
- Projekte können nicht gelöscht werden, solange Beleg-Zuordnungen oder Verkaufspositionen auf sie zeigen.
- Rechnungsnummern sind eindeutig.
- Verkäufe mit Rechnungsdatum, Rechnungsnummer oder Rechnungsdokument können nicht gelöscht oder archiviert werden.
- Soft-Delete wird für `receipt` und `sales_order` verwendet.

## Technische Konventionen
- Geldwerte liegen in `*_cents`.
- Zeitstempel werden in UTC gespeichert.
- Volltextsuche nutzt `receipt_fts`.
- Verkaufsmengen werden als `Decimal(12,3)` gespeichert.

## Initialisierung, Seeds und Migrationen
Initialisierung über `db.init_db()`:
1. Verzeichnisse unter `data/` sicherstellen
2. `SQLModel.metadata.create_all(engine)` für das Grundschema
3. Metatabelle `schema_migration` für angewendete Migrationen sicherstellen
4. fehlende interne Migrationen in stabiler Reihenfolge ausführen
5. resultierenden Schemazustand validieren
6. Initialisierung von FTS
7. Seeds für Default-Kontaktkategorien, Kostenkategorien, Unterkategorien und technische Kostenstelle
8. Anlage wichtiger Indexe

Aktueller Stand:
- Projekte erhalten seit `0.3.6` ihr optionales Feld `notes` über eine additive interne Migration.

Wichtig:
- `data/schema_version.txt` ist keine Wahrheitsquelle für destruktive Aktionen mehr.
- Weder Datenbank noch `data/archive/` werden bei Schemaänderungen automatisch gelöscht.
- Wenn eine Alt-Datenbank nicht sicher migriert oder validiert werden kann, blockiert der Start mit einer gezielten Fehlermeldung.

## Wichtige Indexe
Beispiele:
- `sales_order.contact_id`
- `sales_order.sale_date`
- `sales_order.invoice_date`
- `sales_order.invoice_number`
- `sales_order_item.order_id`
- `sales_order_item.project_id`
- `contact.given_name`, `contact.family_name`, `contact.organisation`
- `contact.street`, `contact.postal_code`, `contact.city`, `contact.country`
- `cost_allocation.receipt_id`, `cost_allocation.cost_type_id`, `cost_allocation.project_id`

## Warum diese Struktur
- klare Trennung zwischen Dokumentkopf und fachlicher Verteilung
- robuste Summenlogik über Integer-Cents
- lokale Volltextsuche ohne externen Dienst
- genügend Struktur für Auswertungen, ohne bereits ein vollständiges Buchhaltungssystem zu sein
