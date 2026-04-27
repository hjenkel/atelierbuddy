# Eigene Rechnungsvorlagen

Atelier Buddy kann automatisch Rechnungs-PDFs aus einer HTML/CSS-Vorlage erzeugen. Standardmäßig wird die mitgelieferte Vorlage verwendet. In den Einstellungen kannst du stattdessen genau eine eigene Vorlage aktivieren.

## Dateien

Eine eigene Vorlage besteht aus:

- `invoice.html` als HTML-Struktur
- `invoice.css` als Styling
- optionalen Font-Dateien im Format `.ttf`, `.otf`, `.woff` oder `.woff2`

Die Dateien werden in Atelier Buddy unter `Einstellungen > Rechnungssteller & Rechnung > Rechnungsvorlage` hochgeladen. HTML und CSS ersetzen jeweils die aktive eigene Datei. Font-Dateien werden nach Dateiname ersetzt, wenn du denselben Namen erneut hochlädst.

## Grenzen

- Die Vorlage wird lokal zu einem PDF gerendert.
- JavaScript wird nicht ausgeführt.
- Externe Abhängigkeiten wie Webfonts, CDN-CSS oder Online-Bilder solltest du nicht voraussetzen.
- Verwende für Fonts relative Pfade aus der CSS-Datei, zum Beispiel `fonts/Atelier.woff2`.
- Wenn die eigene Vorlage aktiviert ist und `invoice.html` oder `invoice.css` fehlt, wird keine Rechnung erzeugt. Atelier Buddy fällt nicht still auf die Standardvorlage zurück.

## Platzhalter

Diese Platzhalter kannst du in `invoice.html` verwenden:

- `$custom_font_face_css`
- `$logo_html`
- `$sender_name`
- `$sender_address_html`
- `$sender_street_line`
- `$sender_city_line`
- `$sender_contact_html`
- `$recipient_html`
- `$invoice_number`
- `$invoice_date`
- `$sale_date`
- `$payment_due_date`
- `$tax_label`
- `$tax_label_footer`
- `$tax_value`
- `$items_html`
- `$total_net`
- `$payment_term_days`
- `$bank_account_holder`
- `$iban`
- `$bic`
- `$currency`
- `$notes`

`$items_html` enthält fertige Tabellenzeilen für die Rechnungspositionen. Lege in deinem HTML dafür eine Tabelle mit passendem `<tbody>` an.

## Beispiel für Fonts

```css
@font-face {
  font-family: "Atelier Display";
  src: url("fonts/Atelier.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
}

body {
  font-family: "Atelier Display", Arial, sans-serif;
}
```

## Promptbeispiel für ChatGPT

```text
Erstelle mir eine druckfähige A4-Rechnungsvorlage für Atelier Buddy.

Bitte liefere zwei Dateien:
1. invoice.html
2. invoice.css

Anforderungen:
- Keine JavaScript-Abhängigkeiten.
- Keine externen CDNs, Webfonts oder Online-Bilder.
- CSS soll für PDF-Rendering mit WeasyPrint geeignet sein.
- Verwende die Platzhalter von Atelier Buddy exakt so:
  $logo_html, $sender_name, $sender_address_html, $sender_street_line,
  $sender_city_line, $sender_contact_html, $recipient_html,
  $invoice_number, $invoice_date, $sale_date, $payment_due_date,
  $tax_label, $tax_label_footer, $tax_value, $items_html, $total_net,
  $payment_term_days, $bank_account_holder, $iban, $bic, $currency, $notes.
- Baue eine Tabelle, deren tbody nur $items_html enthält.
- Die Vorlage soll auf eine Seite A4 passen, aber auch bei mehreren Positionen sauber umbrechen.
- Stil: [beschreibe hier deinen gewünschten Stil, Farben, Schriften und Layout].

Gib mir den Inhalt der beiden Dateien getrennt in Codeblöcken aus.
```

Nach dem Erstellen lädst du `invoice.html`, `invoice.css` und optional deine Font-Dateien in den Einstellungen hoch. Aktiviere anschließend `Eigene Vorlage`.
