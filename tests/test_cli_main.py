from __future__ import annotations

import io

import belegmanager.__main__ as cli_main


def test_main_without_subcommand_runs_app(monkeypatch) -> None:
    called = {"value": False}

    def fake_run() -> None:
        called["value"] = True

    monkeypatch.setattr(cli_main, "run", fake_run)

    assert cli_main.main([]) == 0
    assert called["value"] is True


def test_reset_password_reads_from_stdin(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str]] = []

    class _FakeAuthService:
        def reset_password(self, *, username: str, new_password: str):
            calls.append((username, new_password))
            return type("User", (), {"username": username})()

    monkeypatch.setattr(cli_main, "init_db", lambda: None)
    monkeypatch.setattr(cli_main, "AuthService", lambda: _FakeAuthService())
    monkeypatch.setattr(cli_main.sys, "stdin", io.StringIO("averysecurepass1\n"))

    assert cli_main.main(["reset-password", "--user", "admin", "--password-stdin"]) == 0
    assert calls == [("admin", "averysecurepass1")]
    assert "wurde zurueckgesetzt" in capsys.readouterr().out


def test_reset_password_interactive_rejects_mismatched_confirmation(monkeypatch, capsys) -> None:
    answers = iter(["averysecurepass1", "differentsecurepass2"])
    monkeypatch.setattr(cli_main.getpass, "getpass", lambda prompt: next(answers))

    assert cli_main.main(["reset-password", "--user", "admin"]) == 1
    assert "Passwortbestaetigung" in capsys.readouterr().err


def test_reset_password_returns_error_for_unknown_user(monkeypatch, capsys) -> None:
    class _FakeAuthService:
        def reset_password(self, *, username: str, new_password: str):
            raise ValueError("Benutzerkonto nicht gefunden")

    monkeypatch.setattr(cli_main, "init_db", lambda: None)
    monkeypatch.setattr(cli_main, "AuthService", lambda: _FakeAuthService())
    monkeypatch.setattr(cli_main.sys, "stdin", io.StringIO("averysecurepass1\n"))

    assert cli_main.main(["reset-password", "--user", "missing", "--password-stdin"]) == 1
    assert "nicht gefunden" in capsys.readouterr().err
