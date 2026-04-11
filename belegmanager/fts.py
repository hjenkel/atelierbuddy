from __future__ import annotations

import re

from sqlmodel import Session
from sqlalchemy import text


def init_fts(session: Session) -> None:
    session.exec(
        text(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS receipt_fts
            USING fts5(receipt_id UNINDEXED, content)
            """
        )
    )
    session.commit()


def upsert_fts_row(session: Session, receipt_id: int, content: str) -> None:
    delete_fts_row(session, receipt_id)
    session.exec(
        text("INSERT INTO receipt_fts (receipt_id, content) VALUES (:id, :content)"),
        params={"id": receipt_id, "content": content},
    )


def delete_fts_row(session: Session, receipt_id: int) -> None:
    session.exec(text("DELETE FROM receipt_fts WHERE receipt_id = :id"), params={"id": receipt_id})


def _to_fts_query(user_query: str) -> str:
    tokens = re.findall(r"[\w\-]+", user_query, re.UNICODE)
    if not tokens:
        return ""
    return " AND ".join(f'"{token}"' for token in tokens)


def search_fts_receipt_ids(session: Session, user_query: str) -> list[int]:
    fts_query = _to_fts_query(user_query)
    if not fts_query:
        return []
    rows = session.exec(
        text("SELECT receipt_id FROM receipt_fts WHERE receipt_fts MATCH :query"),
        params={"query": fts_query},
    ).all()
    return [int(row[0]) for row in rows]
