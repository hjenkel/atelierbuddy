from __future__ import annotations

import html
import logging
from urllib.parse import parse_qs, quote, urlparse

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings
from .services.auth_service import AuthService

LOG = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PUBLIC_PREFIXES = ("/_nicegui", "/_nicegui_ws", "/assets", "/favicon.ico")
PUBLIC_ROUTES = {"/login", "/setup", "/logout"}
WS_UNAUTHORIZED_CODE = 4401
WS_FORBIDDEN_CODE = 4403


def sanitize_next_path(path: str | None) -> str:
    raw = (path or "").strip()
    if not raw:
        return "/belege"
    if not raw.startswith("/") or raw.startswith("//"):
        return "/belege"
    if raw.startswith("/login") or raw.startswith("/setup") or raw.startswith("/logout"):
        return "/belege"
    return raw


def is_public_path(path: str) -> bool:
    if path in PUBLIC_ROUTES:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                raw_headers = list(message.get("headers", []))
                if _should_secure_session_cookie(scope):
                    secured_headers = []
                    for name, value in raw_headers:
                        if name.lower() == b"set-cookie" and b"session=" in value and b"secure" not in value.lower():
                            value = value + b"; Secure"
                        secured_headers.append((name, value))
                    raw_headers = secured_headers
                message["headers"] = raw_headers

                headers = MutableHeaders(scope=message)
                headers["X-Frame-Options"] = "DENY"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class OriginValidationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "http":
            request = Request(scope, receive=receive)
            path = request.url.path or "/"
            method = request.method.upper()
            needs_origin_validation = path.startswith("/_nicegui_ws") or method in STATE_CHANGING_METHODS
            if needs_origin_validation and not self._has_valid_origin(
                origin_header=(request.headers.get("origin") or "").strip(),
                referer_header=(request.headers.get("referer") or "").strip(),
                allowed_origins=self._allowed_origins(scope),
            ):
                response = PlainTextResponse("Forbidden", status_code=403)
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if scope_type == "websocket":
            path = (scope.get("path") or "/").strip() or "/"
            if path.startswith("/_nicegui_ws"):
                websocket = WebSocket(scope, receive=receive, send=send)
                if not self._is_allowed_host(self._host_name(scope)):
                    await _close_websocket(send, code=WS_FORBIDDEN_CODE)
                    return
                if not self._has_valid_origin(
                    origin_header=(websocket.headers.get("origin") or "").strip(),
                    referer_header=(websocket.headers.get("referer") or "").strip(),
                    allowed_origins=self._allowed_origins(scope),
                ):
                    await _close_websocket(send, code=WS_FORBIDDEN_CODE)
                    return
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _has_valid_origin(self, *, origin_header: str, referer_header: str, allowed_origins: set[str]) -> bool:
        origin = origin_header or self._origin_from_referer(referer_header)
        if not origin:
            return False
        return origin.lower() in allowed_origins

    def _origin_from_referer(self, referer: str) -> str:
        if not referer:
            return ""
        parsed = urlparse(referer)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    def _allowed_origins(self, scope: Scope) -> set[str]:
        configured = {origin.strip().lower() for origin in settings.allowed_origins if origin.strip()}
        if configured:
            return configured

        host = self._host_header(scope)
        if not host:
            return set()

        scheme = self._origin_scheme(scope)
        if not scheme:
            return set()
        return {f"{scheme}://{host}"}

    def _host_header(self, scope: Scope) -> str:
        return (_scope_header(scope, "host") or "").strip().lower()

    def _host_name(self, scope: Scope) -> str:
        host = self._host_header(scope)
        return host.split(":", 1)[0]

    def _is_allowed_host(self, host_name: str) -> bool:
        host_name = (host_name or "").strip().lower()
        if not host_name:
            return False
        allowed = [host.strip().lower() for host in settings.allowed_hosts if host.strip()]
        if not allowed:
            return True
        if "*" in allowed:
            return True
        for pattern in allowed:
            if host_name == pattern:
                return True
            if pattern.startswith("*.") and host_name.endswith(pattern[1:]):
                return True
        return False

    def _origin_scheme(self, scope: Scope) -> str:
        proto_header = (_scope_header(scope, "x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
        scheme = proto_header or str(scope.get("scheme") or "http").strip().lower()
        if scheme == "wss":
            return "https"
        if scheme == "ws":
            return "http"
        if scheme in {"http", "https"}:
            return scheme
        return "http"


class AuthRequiredMiddleware:
    def __init__(self, app: ASGIApp, *, auth_service: AuthService) -> None:  # type: ignore[override]
        self.app = app
        self.auth_service = auth_service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")
        if scope_type == "http":
            request = Request(scope, receive=receive)
            path = request.url.path or "/"
            method = request.method.upper()
            setup_required = self.auth_service.requires_setup()

            if is_public_path(path):
                if setup_required and path == "/login":
                    response = RedirectResponse("/setup", status_code=303)
                    await response(scope, receive, send)
                    return
                if (not setup_required) and path == "/setup":
                    response = RedirectResponse("/login", status_code=303)
                    await response(scope, receive, send)
                    return
                await self.app(scope, receive, send)
                return

            if setup_required:
                if method in SAFE_METHODS:
                    response = RedirectResponse("/setup", status_code=303)
                else:
                    response = PlainTextResponse("Setup required", status_code=403)
                await response(scope, receive, send)
                return

            user = self.auth_service.session_user(request)
            if user is None:
                if path.startswith("/files/"):
                    response = PlainTextResponse("Unauthorized", status_code=401)
                    await response(scope, receive, send)
                    return
                if method in SAFE_METHODS:
                    next_path = sanitize_next_path(path + (f"?{request.url.query}" if request.url.query else ""))
                    response = RedirectResponse(f"/login?next={quote(next_path, safe='/?=&')}", status_code=303)
                    await response(scope, receive, send)
                    return
                response = PlainTextResponse("Unauthorized", status_code=401)
                await response(scope, receive, send)
                return

            request.state.current_user = user
            await self.app(scope, receive, send)
            return

        if scope_type == "websocket":
            path = (scope.get("path") or "/").strip() or "/"
            if path.startswith("/_nicegui_ws"):
                setup_required = self.auth_service.requires_setup()
                if setup_required:
                    await _close_websocket(send, code=WS_FORBIDDEN_CODE)
                    return

                websocket = WebSocket(scope, receive=receive, send=send)
                user = self.auth_service.session_user(websocket)
                if user is None:
                    await _close_websocket(send, code=WS_UNAUTHORIZED_CODE)
                    return

                state = scope.setdefault("state", {})
                if isinstance(state, dict):
                    state["current_user"] = user

            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, send)


def register_auth_routes(auth_service: AuthService) -> None:
    from nicegui import app

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request) -> HTMLResponse:
        if auth_service.requires_setup():
            return RedirectResponse("/setup", status_code=303)
        if auth_service.session_user(request):
            next_path = sanitize_next_path(request.query_params.get("next"))
            return RedirectResponse(next_path, status_code=303)
        return HTMLResponse(
            _login_html(next_path=sanitize_next_path(request.query_params.get("next")), error_message=None),
            status_code=200,
        )

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request) -> Response:
        if auth_service.requires_setup():
            return RedirectResponse("/setup", status_code=303)
        data = await _parse_form_data(request)
        username = data.get("username", "")
        password = data.get("password", "")
        next_path = sanitize_next_path(data.get("next"))
        user = auth_service.authenticate(
            username=username,
            password=password,
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        if not user:
            return HTMLResponse(
                _login_html(
                    next_path=next_path,
                    error_message="Login fehlgeschlagen. Bitte Eingaben prüfen oder später erneut versuchen.",
                ),
                status_code=401,
            )
        auth_service.start_session(request, user)
        return RedirectResponse(next_path, status_code=303)

    @app.get("/setup", include_in_schema=False)
    async def setup_page(request: Request) -> Response:
        if not auth_service.requires_setup():
            if auth_service.session_user(request):
                return RedirectResponse("/belege", status_code=303)
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(_setup_html(error_message=None), status_code=200)

    @app.post("/setup", include_in_schema=False)
    async def setup_submit(request: Request) -> Response:
        if not auth_service.requires_setup():
            return RedirectResponse("/login", status_code=303)
        data = await _parse_form_data(request)
        username = data.get("username", "")
        password = data.get("password", "")
        confirm_password = data.get("confirm_password", "")
        if password != confirm_password:
            return HTMLResponse(_setup_html(error_message="Passwörter stimmen nicht überein."), status_code=400)
        try:
            user = auth_service.create_initial_admin(
                username=username,
                password=password,
                client_ip=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
            )
        except ValueError as exc:
            return HTMLResponse(_setup_html(error_message=str(exc)), status_code=400)
        except Exception:
            error_id = secrets_token_short()
            LOG.exception("Setup fehlgeschlagen (ID %s)", error_id)
            return HTMLResponse(
                _setup_html(error_message=f"Setup fehlgeschlagen. Fehler-ID: {error_id}"),
                status_code=500,
            )

        auth_service.start_session(request, user)
        return RedirectResponse("/belege", status_code=303)

    @app.get("/logout", include_in_schema=False)
    async def logout_get(request: Request) -> RedirectResponse:
        auth_service.end_session(request)
        return RedirectResponse("/login", status_code=303)

    @app.post("/logout", include_in_schema=False)
    async def logout_post(request: Request) -> RedirectResponse:
        auth_service.end_session(request)
        return RedirectResponse("/login", status_code=303)


async def _parse_form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", errors="ignore")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _client_ip(request: Request) -> str | None:
    if request.client and request.client.host:
        return request.client.host
    return None


def secrets_token_short() -> str:
    import secrets

    return secrets.token_hex(4)


async def _close_websocket(send: Send, *, code: int) -> None:
    await send({"type": "websocket.close", "code": code})


def _scope_header(scope: Scope, key: str) -> str:
    lookup = key.lower().encode("latin1")
    for name, value in scope.get("headers", []):
        if name.lower() == lookup:
            try:
                return value.decode("latin1")
            except Exception:
                return ""
    return ""


def _should_secure_session_cookie(scope: Scope) -> bool:
    mode = settings.secure_cookies
    if mode == "true":
        return True
    if mode == "false":
        return False
    proto_header = (_scope_header(scope, "x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = proto_header or str(scope.get("scheme") or "http").strip().lower()
    if scheme == "wss":
        scheme = "https"
    if scheme == "ws":
        scheme = "http"
    return scheme == "https"


def _base_html(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f5f1ea;
      --card: #fff;
      --text: #1f160f;
      --muted: #5a4b3f;
      --border: #1f160f;
      --primary: #5c30ff;
      --danger: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: radial-gradient(circle at 10% 0%, #fff3dc 0%, var(--bg) 48%, #f0e4d2 100%);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      display: grid;
      place-items: center;
      padding: 24px;
    }}
    .card {{
      width: min(480px, 100%);
      background: var(--card);
      border: 2px solid var(--border);
      border-radius: 14px;
      box-shadow: 5px 5px 0 var(--border);
      padding: 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.45rem;
      line-height: 1.2;
    }}
    p {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    label {{
      display: block;
      margin: 10px 0 6px;
      font-weight: 600;
      font-size: 0.92rem;
    }}
    input {{
      width: 100%;
      border: 2px solid #d6cabc;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 0.95rem;
    }}
    input:focus {{
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 2px #e6defe;
    }}
    .error {{
      margin: 0 0 12px;
      border: 1px solid #f1b4ad;
      background: #fdeceb;
      color: var(--danger);
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 0.92rem;
      font-weight: 600;
    }}
    button {{
      margin-top: 14px;
      width: 100%;
      border: 2px solid var(--border);
      border-radius: 10px;
      background: var(--primary);
      color: #fff;
      font-size: 0.95rem;
      font-weight: 700;
      padding: 10px 12px;
      cursor: pointer;
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>
"""


def _submit_on_enter_form_attrs(form_id: str) -> str:
    safe_form_id = html.escape(form_id, quote=True)
    return (
        f'id="{safe_form_id}" '
        "onkeydown=\"if (event.key === 'Enter' && event.target instanceof HTMLElement "
        "&& event.target.tagName !== 'TEXTAREA') { event.preventDefault(); this.requestSubmit(); }\""
    )


def _login_html(*, next_path: str, error_message: str | None) -> str:
    safe_error = f'<div class="error">{html.escape(error_message)}</div>' if error_message else ""
    safe_next = html.escape(next_path, quote=True)
    body = f"""
<div class="card">
  <h1>Anmeldung</h1>
  <p>Bitte mit deinem Atelier-Buddy-Konto anmelden.</p>
  {safe_error}
  <form {_submit_on_enter_form_attrs("login-form")} method="post" action="/login">
    <input type="hidden" name="next" value="{safe_next}" />
    <label for="username">Benutzername</label>
    <input id="username" name="username" type="text" autocomplete="username" required />
    <label for="password">Passwort</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required />
    <button type="submit">Anmelden</button>
  </form>
</div>
"""
    return _base_html("Anmeldung", body)


def _setup_html(*, error_message: str | None) -> str:
    safe_error = f'<div class="error">{html.escape(error_message)}</div>' if error_message else ""
    body = f"""
<div class="card">
  <h1>Ersteinrichtung</h1>
  <p>Lege den ersten Admin-Zugang an. Danach ist die Ersteinrichtung geschlossen und die Anmeldung läuft normal über den Login.</p>
  {safe_error}
  <form {_submit_on_enter_form_attrs("setup-form")} method="post" action="/setup">
    <label for="username">Benutzername</label>
    <input id="username" name="username" type="text" autocomplete="username" required />
    <label for="password">Passwort</label>
    <input id="password" name="password" type="password" autocomplete="new-password" required />
    <label for="confirm_password">Passwort wiederholen</label>
    <input id="confirm_password" name="confirm_password" type="password" autocomplete="new-password" required />
    <button type="submit">Admin anlegen</button>
  </form>
</div>
"""
    return _base_html("Ersteinrichtung", body)
