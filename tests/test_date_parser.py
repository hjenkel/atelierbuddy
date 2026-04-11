from datetime import date

from belegmanager.utils.date_parser import parse_document_date


def test_parse_document_date_german_format() -> None:
    text = "Rechnung vom 14.03.2026 fuer Projekt Klang"
    assert parse_document_date(text) == date(2026, 3, 14)


def test_parse_document_date_iso_format() -> None:
    text = "Issued: 2025-11-02"
    assert parse_document_date(text) == date(2025, 11, 2)


def test_parse_document_date_none() -> None:
    text = "Ohne Datum im Dokument"
    assert parse_document_date(text) is None
