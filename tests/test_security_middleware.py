from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import WebSocketDisconnect
from starlette.middleware.trustedhost import TrustedHostMiddleware

from belegmanager.models import AppUser
from belegmanager.security import AuthRequiredMiddleware, OriginValidationMiddleware
import belegmanager.security as security_module
from belegmanager.services.auth_service import AuthService


def _build_app(*, with_user: bool) -> FastAPI:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    auth_service = AuthService(db_engine=engine)
    if with_user:
        auth_service.create_initial_admin(username="admin", password="supersecure123")

    app = FastAPI()
    app.add_middleware(AuthRequiredMiddleware, auth_service=auth_service)
    app.add_middleware(OriginValidationMiddleware)
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/setup")
    async def setup_page() -> PlainTextResponse:
        return PlainTextResponse("setup")

    @app.get("/login")
    async def login_page() -> PlainTextResponse:
        return PlainTextResponse("login")

    @app.post("/login")
    async def login_submit(request: Request) -> PlainTextResponse:
        with Session(engine) as session:
            user = session.exec(select(AppUser)).first()
            assert user is not None
        auth_service.start_session(request, user)
        return PlainTextResponse("ok")

    @app.get("/belege")
    async def protected_page() -> PlainTextResponse:
        return PlainTextResponse("protected")

    @app.get("/_nicegui_ws/test")
    async def ws_like_path() -> PlainTextResponse:
        return PlainTextResponse("ws")

    @app.websocket("/_nicegui_ws/test")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("ws")
        await websocket.close()

    return app


def test_auth_middleware_redirects_to_setup_when_no_user_exists() -> None:
    client = TestClient(_build_app(with_user=False))
    response = client.get("/belege", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_setup_route_is_public_only_until_first_user_exists() -> None:
    client = TestClient(_build_app(with_user=False))
    setup_response = client.get("/setup")
    assert setup_response.status_code == 200
    assert setup_response.text == "setup"

    app = _build_app(with_user=True)
    configured_client = TestClient(app)
    configured_response = configured_client.get("/setup", follow_redirects=False)
    assert configured_response.status_code == 303
    assert configured_response.headers["location"] == "/login"


def test_auth_middleware_redirects_to_login_and_allows_after_session() -> None:
    client = TestClient(_build_app(with_user=True))
    response = client.get("/belege", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

    login_response = client.post("/login", headers={"origin": "http://testserver"})
    assert login_response.status_code == 200

    protected_response = client.get("/belege")
    assert protected_response.status_code == 200
    assert protected_response.text == "protected"


def test_origin_middleware_blocks_missing_origin_for_state_change_and_ws_path() -> None:
    client = TestClient(_build_app(with_user=True))

    no_origin_post = client.post("/login")
    assert no_origin_post.status_code == 403

    no_origin_ws = client.get("/_nicegui_ws/test")
    assert no_origin_ws.status_code == 403


def test_ws_requires_auth_session() -> None:
    client = TestClient(_build_app(with_user=True), base_url="http://localhost")
    try:
        with client.websocket_connect(
            "/_nicegui_ws/test",
            headers={"host": "localhost", "origin": "http://localhost"},
        ):
            raise AssertionError("websocket should be rejected without auth session")
    except WebSocketDisconnect as exc:
        assert exc.code == 4401


def test_ws_rejected_when_setup_is_required() -> None:
    client = TestClient(_build_app(with_user=False), base_url="http://localhost")
    try:
        with client.websocket_connect(
            "/_nicegui_ws/test",
            headers={"host": "localhost", "origin": "http://localhost"},
        ):
            raise AssertionError("websocket should be rejected during setup mode")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403


def test_ws_rejects_missing_or_invalid_origin() -> None:
    client = TestClient(_build_app(with_user=True), base_url="http://localhost")
    login_response = client.post("/login", headers={"origin": "http://localhost"})
    assert login_response.status_code == 200
    session_cookie = client.cookies.get("session")
    assert session_cookie

    try:
        with client.websocket_connect(
            "/_nicegui_ws/test",
            headers={"host": "localhost", "cookie": f"session={session_cookie}"},
        ):
            raise AssertionError("websocket should require origin")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403

    try:
        with client.websocket_connect(
            "/_nicegui_ws/test",
            headers={
                "host": "localhost",
                "origin": "http://evil.example",
                "cookie": f"session={session_cookie}",
            },
        ):
            raise AssertionError("websocket should reject invalid origin")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403


def test_ws_accepts_valid_origin_and_session() -> None:
    client = TestClient(_build_app(with_user=True), base_url="http://localhost")
    login_response = client.post("/login", headers={"origin": "http://localhost"})
    assert login_response.status_code == 200
    session_cookie = client.cookies.get("session")
    assert session_cookie

    with client.websocket_connect(
        "/_nicegui_ws/test",
        headers={
            "host": "localhost",
            "origin": "http://localhost",
            "cookie": f"session={session_cookie}",
        },
    ) as websocket:
        assert websocket.receive_text() == "ws"


def test_ws_rejects_invalid_host(monkeypatch) -> None:
    monkeypatch.setattr(
        security_module,
        "settings",
        replace(security_module.settings, allowed_hosts=("localhost",)),
    )
    client = TestClient(_build_app(with_user=True), base_url="http://localhost")
    login_response = client.post("/login", headers={"origin": "http://localhost"})
    assert login_response.status_code == 200
    session_cookie = client.cookies.get("session")
    assert session_cookie

    try:
        with client.websocket_connect(
            "/_nicegui_ws/test",
            headers={
                "host": "evil.example",
                "origin": "http://evil.example",
                "cookie": f"session={session_cookie}",
            },
        ):
            raise AssertionError("websocket should reject invalid host")
    except WebSocketDisconnect as exc:
        assert exc.code == 4403


def test_trusted_host_middleware_rejects_unexpected_host() -> None:
    app = FastAPI()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["testserver"])

    @app.get("/ping")
    async def ping() -> PlainTextResponse:
        return PlainTextResponse("pong")

    client = TestClient(app, base_url="http://evil.example")
    response = client.get("/ping", follow_redirects=False)
    assert response.status_code == 400
