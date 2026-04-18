from __future__ import annotations

import argparse
import getpass
import sys

from .db import init_db
from .main import run
from .services.auth_service import AuthService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m belegmanager")
    subparsers = parser.add_subparsers(dest="command")

    reset_parser = subparsers.add_parser("reset-password", help="Lokales Passwort eines Users zuruecksetzen")
    reset_parser.add_argument("--user", required=True, help="Benutzername des Accounts")
    reset_parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Neues Passwort aus stdin lesen statt interaktiv abzufragen",
    )
    return parser


def _read_new_password(*, password_stdin: bool) -> str:
    if password_stdin:
        password = sys.stdin.read().strip()
        if not password:
            raise ValueError("Kein Passwort ueber stdin erhalten")
        return password

    password = getpass.getpass("Neues Passwort: ")
    confirm_password = getpass.getpass("Neues Passwort wiederholen: ")
    if password != confirm_password:
        raise ValueError("Passwortbestaetigung stimmt nicht ueberein")
    return password


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command != "reset-password":
        run()
        return 0

    try:
        new_password = _read_new_password(password_stdin=bool(args.password_stdin))
        init_db()
        auth_service = AuthService()
        user = auth_service.reset_password(username=args.user, new_password=new_password)
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr)
        return 130

    print(f"Passwort fuer '{user.username}' wurde zurueckgesetzt. Bestehende Sitzungen sind nun ungueltig.")
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
