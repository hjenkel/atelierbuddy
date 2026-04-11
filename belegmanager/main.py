from __future__ import annotations

import logging
import secrets

from nicegui import app, ui
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .app_state import get_services
from .config import settings
from .db import init_db
from .security import AuthRequiredMiddleware, OriginValidationMiddleware, SecurityHeadersMiddleware, register_auth_routes
from .ui import apply_theme, register_pages

LOG = logging.getLogger(__name__)


def run() -> None:
    settings.ensure_dirs()
    init_db()

    app.add_static_files("/assets", str(settings.assets_dir))
    app.add_static_files("/files", str(settings.archive_dir))

    allowed_hosts = [host.strip() for host in settings.allowed_hosts if host.strip()]
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
        if "*" in allowed_hosts:
            LOG.warning(
                "BM_ALLOWED_HOSTS erlaubt aktuell alle Host-Header ('*'). "
                "Setze BM_ALLOWED_HOSTS restriktiver fuer haertere Produktionseinstellungen."
            )

    apply_theme()
    services = get_services()
    register_auth_routes(services.auth_service)
    register_pages(services)

    app.add_middleware(AuthRequiredMiddleware, auth_service=services.auth_service)
    app.add_middleware(OriginValidationMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.on_page_exception(lambda _: ui.label("Ein interner Fehler ist aufgetreten."))

    session_secret = settings.session_secret.strip()
    if not session_secret:
        session_secret = secrets.token_urlsafe(32)
        LOG.warning(
            "BM_SESSION_SECRET nicht gesetzt; es wurde ein temporäres Secret generiert. "
            "Setze BM_SESSION_SECRET für stabile Sessions über Neustarts hinweg."
        )

    ui.run(
        title="Atelier Buddy",
        favicon=settings.assets_dir / "hamster-favicon.png",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        storage_secret=session_secret,
        session_middleware_kwargs={
            "same_site": "lax",
            "https_only": settings.secure_cookies == "true",
            "max_age": int(settings.session_max_age_hours) * 3600,
        },
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
