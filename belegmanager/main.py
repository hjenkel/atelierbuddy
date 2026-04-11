from __future__ import annotations

from nicegui import app, ui

from .app_state import get_services
from .config import settings
from .db import init_db
from .ui import apply_theme, register_pages


def run() -> None:
    settings.ensure_dirs()
    init_db()

    app.add_static_files("/assets", str(settings.assets_dir))
    app.add_static_files("/files", str(settings.data_dir))

    apply_theme()
    services = get_services()
    register_pages(services)

    ui.run(
        title="Atelier Buddy",
        favicon=settings.assets_dir / "hamster-favicon.svg",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
