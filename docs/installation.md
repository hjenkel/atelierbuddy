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

Im Docker-Betrieb mit benanntem Volume sollte das Volume bzw. der gemountete Datenpfad gesichert werden.

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
- Aenderungen am lokalen Code koennen durch ein erneutes `docker compose up --build -d` ins Image uebernommen werden.
- Auch hier bleiben Daten im Volume `atelier_buddy_data` erhalten.

### Daten, Volumes und Backups im Docker-Betrieb
Im Docker-Betrieb liegen die persistenten Anwendungsdaten nicht im Container selbst, sondern im Volume oder im gemounteten Datenpfad unter `/app/data`.

Dazu gehoeren insbesondere:
- die SQLite-Datenbank
- importierte und archivierte Dateien
- erzeugte Thumbnails und OCR-Artefakte

Wichtig:
- Container können jederzeit neu erstellt werden, ohne dass diese Daten verloren gehen, solange das Volume erhalten bleibt.
- Backups sollten unbedingt das Volume beziehungsweise den Datenpfad sichern.

### Ersteinrichtung nach der Installation
Beim ersten Start wird im Browser direkt der User angelegt.
