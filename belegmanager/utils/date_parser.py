from __future__ import annotations

import re
from datetime import date, datetime

from dateutil import parser as dt_parser

DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b"),
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
]


def _is_reasonable(candidate: date) -> bool:
    this_year = datetime.now().year
    return 1990 <= candidate.year <= this_year + 1


def parse_document_date(text: str) -> date | None:
    if not text:
        return None

    for index, pattern in enumerate(DATE_PATTERNS):
        match = pattern.search(text)
        if not match:
            continue
        parts = [int(part) for part in match.groups()]
        try:
            if index == 1:  # yyyy-mm-dd
                candidate = date(parts[0], parts[1], parts[2])
            else:  # dd.mm.yyyy / dd-mm-yyyy / dd/mm/yyyy
                candidate = date(parts[2], parts[1], parts[0])
        except ValueError:
            continue
        if _is_reasonable(candidate):
            return candidate

    try:
        fuzzy = dt_parser.parse(text, dayfirst=True, fuzzy=True).date()
        if _is_reasonable(fuzzy):
            return fuzzy
    except (ValueError, TypeError, OverflowError):
        return None

    return None
