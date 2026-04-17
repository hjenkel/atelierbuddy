from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import SQLModel, Session, create_engine, select

from belegmanager.models import AppUser
from belegmanager.services.auth_service import AuthService


def _build_auth_service() -> tuple[AuthService, object]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return AuthService(db_engine=engine), engine


class _RequestStub:
    def __init__(self) -> None:
        self.session: dict[str, object] = {}


def test_create_initial_admin_hashes_password_and_disables_setup() -> None:
    service, engine = _build_auth_service()
    user = service.create_initial_admin(username="admin", password="supersecure123")
    assert user.id is not None
    assert user.password_hash != "supersecure123"
    assert not service.requires_setup()

    with Session(engine) as session:
        stored = session.exec(select(AppUser).where(AppUser.id == user.id)).first()
        assert stored is not None
        assert stored.username == "admin"


def test_create_initial_admin_rejects_when_setup_is_already_completed() -> None:
    service, _ = _build_auth_service()
    service.create_initial_admin(username="admin", password="supersecure123")

    try:
        service.create_initial_admin(username="admin2", password="supersecure456")
    except ValueError as exc:
        assert "bereits abgeschlossen" in str(exc)
    else:
        raise AssertionError("expected ValueError when setup is already completed")


def test_authenticate_locks_user_after_repeated_failures() -> None:
    service, engine = _build_auth_service()
    service.create_initial_admin(username="artist", password="averysecurepass1")

    for _ in range(service.MAX_FAILED_ATTEMPTS):
        assert (
            service.authenticate(
                username="artist",
                password="wrongpassword",
                client_ip="127.0.0.1",
                user_agent="pytest",
            )
            is None
        )

    with Session(engine) as session:
        user = session.exec(select(AppUser).where(AppUser.username == "artist")).first()
        assert user is not None
        assert user.locked_until is not None
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        assert locked_until > datetime.now(timezone.utc)


def test_session_user_expires_for_idle_timeout() -> None:
    service, _ = _build_auth_service()
    user = service.create_initial_admin(username="idleuser", password="averysecurepass2")

    request = _RequestStub()
    service.start_session(request, user)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    request.session[service.SESSION_LAST_SEEN_AT] = now_ts - ((8 * 60 * 60) + 120)
    request.session[service.SESSION_LOGIN_AT] = now_ts

    assert service.session_user(request) is None
    assert service.session_user_id(request) is None


def test_session_user_expires_for_absolute_timeout() -> None:
    service, _ = _build_auth_service()
    user = service.create_initial_admin(username="ageduser", password="averysecurepass3")

    request = _RequestStub()
    service.start_session(request, user)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    request.session[service.SESSION_LOGIN_AT] = now_ts - ((7 * 24 * 60 * 60) + 120)
    request.session[service.SESSION_LAST_SEEN_AT] = now_ts

    assert service.session_user(request) is None
