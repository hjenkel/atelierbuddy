from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import func
from sqlmodel import Session, select

from ..config import settings
from ..db import engine
from ..models import AppUser, AuthAttempt


class AuthService:
    MIN_PASSWORD_LENGTH = 12
    MAX_PASSWORD_LENGTH = 128
    MIN_USERNAME_LENGTH = 3
    MAX_USERNAME_LENGTH = 64
    MAX_FAILED_ATTEMPTS = 5
    ATTEMPT_WINDOW_MINUTES = 15
    LOCK_MINUTES = 15

    SESSION_USER_ID = "bm_auth_user_id"
    SESSION_LOGIN_AT = "bm_auth_login_at"
    SESSION_LAST_SEEN_AT = "bm_auth_last_seen_at"

    def __init__(self, db_engine=engine) -> None:
        self._engine = db_engine
        self._password_hasher = PasswordHasher()

    def has_users(self) -> bool:
        with Session(self._engine) as session:
            count = session.exec(select(func.count()).select_from(AppUser)).one()
        return int(count or 0) > 0

    def requires_setup(self) -> bool:
        return not self.has_users()

    def normalize_username(self, raw: str) -> str:
        username = (raw or "").strip()
        if len(username) < self.MIN_USERNAME_LENGTH or len(username) > self.MAX_USERNAME_LENGTH:
            raise ValueError(
                f"Benutzername muss zwischen {self.MIN_USERNAME_LENGTH} und {self.MAX_USERNAME_LENGTH} Zeichen lang sein"
            )
        if re.search(r"\s", username):
            raise ValueError("Benutzername darf keine Leerzeichen enthalten")
        return username

    def validate_password(self, raw: str) -> str:
        password = raw or ""
        if len(password) < self.MIN_PASSWORD_LENGTH or len(password) > self.MAX_PASSWORD_LENGTH:
            raise ValueError(
                f"Passwort muss zwischen {self.MIN_PASSWORD_LENGTH} und {self.MAX_PASSWORD_LENGTH} Zeichen lang sein"
            )
        return password

    def create_initial_admin(
        self,
        *,
        username: str,
        password: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> AppUser:
        if not self.requires_setup():
            raise ValueError("Setup ist bereits abgeschlossen")

        normalized_username = self.normalize_username(username)
        validated_password = self.validate_password(password)
        now = datetime.now(timezone.utc)

        with Session(self._engine) as session:
            duplicate = session.exec(
                select(AppUser).where(func.lower(AppUser.username) == normalized_username.casefold())
            ).first()
            if duplicate:
                raise ValueError("Benutzername existiert bereits")

            user = AppUser(
                username=normalized_username,
                password_hash=self._password_hasher.hash(validated_password),
                active=True,
                is_admin=True,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            session.flush()
            session.add(
                AuthAttempt(
                    username=normalized_username,
                    user_id=user.id,
                    successful=True,
                    attempted_at=now,
                    client_ip=(client_ip or "")[:64] or None,
                    user_agent=(user_agent or "")[:400] or None,
                )
            )
            session.commit()
            session.refresh(user)

        return user

    def change_password(self, *, user_id: int, current_password: str, new_password: str) -> AppUser:
        validated_password = self.validate_password(new_password)
        if not current_password:
            raise ValueError("Aktuelles Passwort ist erforderlich")

        with Session(self._engine) as session:
            user = session.get(AppUser, user_id)
            if not user or not user.active:
                raise ValueError("Benutzerkonto nicht gefunden")
            try:
                self._password_hasher.verify(user.password_hash, current_password)
            except VerifyMismatchError as exc:
                raise ValueError("Aktuelles Passwort ist falsch") from exc
            except Exception as exc:
                raise ValueError("Aktuelles Passwort konnte nicht geprüft werden") from exc
            updated_user = self._set_password(session, user=user, new_password=validated_password)
            session.commit()
            session.refresh(updated_user)
            return updated_user

    def reset_password(self, *, username: str, new_password: str) -> AppUser:
        normalized_username = self.normalize_username(username)
        validated_password = self.validate_password(new_password)

        with Session(self._engine) as session:
            user = session.exec(
                select(AppUser).where(func.lower(AppUser.username) == normalized_username.casefold())
            ).first()
            if not user:
                raise ValueError("Benutzerkonto nicht gefunden")
            updated_user = self._set_password(session, user=user, new_password=validated_password)
            session.commit()
            session.refresh(updated_user)
            return updated_user

    def authenticate(
        self,
        *,
        username: str,
        password: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> AppUser | None:
        normalized_username = (username or "").strip()
        if not normalized_username or not password:
            self._record_attempt(
                username=normalized_username or "<empty>",
                user_id=None,
                successful=False,
                client_ip=client_ip,
                user_agent=user_agent,
            )
            return None

        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            user = session.exec(
                select(AppUser).where(func.lower(AppUser.username) == normalized_username.casefold())
            ).first()
            if not user or not user.active:
                self._record_attempt(
                    username=normalized_username,
                    user_id=user.id if user else None,
                    successful=False,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    session=session,
                    attempted_at=now,
                )
                session.commit()
                return None

            locked_until = self._as_utc(user.locked_until)
            if locked_until and locked_until > now:
                self._record_attempt(
                    username=normalized_username,
                    user_id=user.id,
                    successful=False,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    session=session,
                    attempted_at=now,
                )
                session.commit()
                return None

            try:
                self._password_hasher.verify(user.password_hash, password)
            except VerifyMismatchError:
                self._record_attempt(
                    username=normalized_username,
                    user_id=user.id,
                    successful=False,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    session=session,
                    attempted_at=now,
                )
                if self._failed_attempts_since(session, normalized_username, now=now) >= self.MAX_FAILED_ATTEMPTS:
                    user.locked_until = now + timedelta(minutes=self.LOCK_MINUTES)
                    user.updated_at = now
                    session.add(user)
                session.commit()
                return None
            except Exception:
                self._record_attempt(
                    username=normalized_username,
                    user_id=user.id,
                    successful=False,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    session=session,
                    attempted_at=now,
                )
                session.commit()
                return None

            user.locked_until = None
            user.last_login_at = now
            user.updated_at = now
            session.add(user)
            self._record_attempt(
                username=normalized_username,
                user_id=user.id,
                successful=True,
                client_ip=client_ip,
                user_agent=user_agent,
                session=session,
                attempted_at=now,
            )
            session.commit()
            session.refresh(user)
            return user

    def start_session(self, request: Any, user: AppUser) -> None:
        now = int(datetime.now(timezone.utc).timestamp())
        request.session[self.SESSION_USER_ID] = int(user.id)
        request.session[self.SESSION_LOGIN_AT] = now
        request.session[self.SESSION_LAST_SEEN_AT] = now

    def end_session(self, request: Any) -> None:
        request.session.pop(self.SESSION_USER_ID, None)
        request.session.pop(self.SESSION_LOGIN_AT, None)
        request.session.pop(self.SESSION_LAST_SEEN_AT, None)

    def session_user_id(self, request: Any) -> int | None:
        raw = request.session.get(self.SESSION_USER_ID)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def session_user(self, request: Any) -> AppUser | None:
        user_id = self.session_user_id(request)
        if not user_id:
            return None

        login_at = self._to_int(request.session.get(self.SESSION_LOGIN_AT))
        last_seen_at = self._to_int(request.session.get(self.SESSION_LAST_SEEN_AT))
        if not login_at or not last_seen_at:
            self.end_session(request)
            return None

        now_ts = int(datetime.now(timezone.utc).timestamp())
        idle_limit = int(settings.session_idle_minutes) * 60
        absolute_limit = int(settings.session_max_age_hours) * 3600
        if (now_ts - last_seen_at) > idle_limit or (now_ts - login_at) > absolute_limit:
            self.end_session(request)
            return None

        with Session(self._engine) as session:
            user = session.get(AppUser, user_id)
            if not user or not user.active:
                self.end_session(request)
                return None
            password_changed_at = self._as_utc(user.password_changed_at)
            if password_changed_at and login_at <= int(password_changed_at.timestamp()):
                self.end_session(request)
                return None
            locked_until = self._as_utc(user.locked_until)
            if locked_until and locked_until > datetime.now(timezone.utc):
                self.end_session(request)
                return None
            request.session[self.SESSION_LAST_SEEN_AT] = now_ts
            return user

    def _set_password(self, session: Session, *, user: AppUser, new_password: str) -> AppUser:
        now = datetime.now(timezone.utc)
        user.password_hash = self._password_hasher.hash(new_password)
        user.locked_until = None
        user.password_changed_at = now
        user.updated_at = now
        session.add(user)
        return user

    def _failed_attempts_since(self, session: Session, username: str, *, now: datetime) -> int:
        since = now - timedelta(minutes=self.ATTEMPT_WINDOW_MINUTES)
        count = session.exec(
            select(func.count())
            .select_from(AuthAttempt)
            .where(
                func.lower(AuthAttempt.username) == username.casefold(),
                AuthAttempt.successful.is_(False),
                AuthAttempt.attempted_at >= since,
            )
        ).one()
        return int(count or 0)

    def _record_attempt(
        self,
        *,
        username: str,
        user_id: int | None,
        successful: bool,
        client_ip: str | None = None,
        user_agent: str | None = None,
        session: Session | None = None,
        attempted_at: datetime | None = None,
    ) -> None:
        own_session = session is None
        if session is None:
            session = Session(self._engine)
        try:
            session.add(
                AuthAttempt(
                    username=(username or "")[:120],
                    user_id=user_id,
                    successful=successful,
                    attempted_at=attempted_at or datetime.now(timezone.utc),
                    client_ip=(client_ip or "")[:64] or None,
                    user_agent=(user_agent or "")[:400] or None,
                )
            )
            if own_session:
                session.commit()
        finally:
            if own_session:
                session.close()

    def _to_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _as_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
