# Installation

Für die Installation per Docker bitte zunächst die Hinweise in der [README](../README.md) lesen.

## Anpassen der Docker Compose
In der Docker Compose können folgende ENV-Parameter unter `environment:` gesetzt werden:

- `BM_SESSION_SECRET`: Secret für signierte Session-Cookies
- `BM_ALLOWED_HOSTS`: Host-Allowlist, Default `*`
- `BM_ALLOWED_ORIGINS`: optionale Origin-Allowlist
- `BM_SESSION_IDLE_MINUTES`: Default `480`
- `BM_SESSION_MAX_AGE_HOURS`: Default `168`
- `BM_SECURE_COOKIES`: `auto`, `true` oder `false`
- `BM_MAX_UPLOAD_MB`: Default `25`
- `BM_OCR_TIMEOUT_SECONDS`: Default `300`
- `BM_OCR_LANGUAGES`: Default `deu+eng`

## Daten und Backups bei lokaler Installation
Persistente Laufzeitdaten:
- `data/belegmanager.db`
- `data/archive/`

Einfaches Backup:
```bash
tar -czf atelierbuddy-backup-$(date +%Y-%m-%d).tar.gz data
```

Im Docker-Betrieb mit der empfohlenen Compose-Datei solltest du den Ordner `./data` sichern.
Das ist der Ordner neben deiner `docker-compose.yml`.

## Docker Compose mit lokalem Build
Diese Variante baut das Image direkt aus dem ausgecheckten Repository. Sie eignet sich für Entwicklung, Tests oder für Installationen, bei denen bewusst der lokale Quellcode verwendet werden soll.

Start:

```bash
git clone https://github.com/hjenkel/atelierbuddy.git
cd atelierbuddy
echo "BM_SESSION_SECRET=$(openssl rand -hex 32)" > .env
docker compose up --build -d
```

Hinweise:
- Diese Variante nutzt die im Repository enthaltene [docker-compose.yml](./../docker-compose.yml).
- Änderungen am lokalen Code können durch ein erneutes `docker compose up --build -d` ins Image übernommen werden.
- Auch hier bleiben Daten im Ordner `./data` erhalten.

### Daten, Volumes und Backups im Docker-Betrieb
Im Docker-Betrieb liegen die persistenten Anwendungsdaten nicht im Container selbst.
Sie liegen im Host-Ordner `./data`, der im Container nach `/app/data` gemountet wird.

Dazu gehören insbesondere:
- die SQLite-Datenbank
- importierte und archivierte Dateien
- erzeugte Thumbnails und OCR-Artefakte

Wichtig:
- Container können jederzeit neu erstellt werden, ohne dass diese Daten verloren gehen, solange der Ordner `./data` erhalten bleibt.
- Für ein Backup reicht es, den Ordner `./data` zu sichern.

### Ersteinrichtung nach der Installation
Beim ersten Start wird im Browser direkt der User angelegt.

## Kennwort-Reset
Atelier Buddy bietet derzeit keinen E-Mail-gestützten `Passwort vergessen`-Flow. Für lokale Installationen und Self-Hosting erfolgt der Reset über die CLI auf dem Gerät bzw. im Container.

Lokal:

```bash
python -m belegmanager reset-password --user <benutzername>
```

Docker:

```bash
docker compose exec atelier-buddy python -m belegmanager reset-password --user <benutzername>
```

Für Automatisierung oder Skripte kann das neue Passwort über `stdin` gelesen werden:

```bash
printf '%s' 'NEUES-LANGES-PASSWORT' | docker compose exec -T atelier-buddy python -m belegmanager reset-password --user <benutzername> --password-stdin
```

Hinweise:
- Nach erfolgreichem Reset sind bestehende Sitzungen des Users ungültig.
- Der Reset ändert nur das Kennwort; Datenbank und Archivdaten unter `data/` bzw. `/app/data` bleiben unverändert.
