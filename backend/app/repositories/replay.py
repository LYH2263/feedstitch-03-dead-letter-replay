"""Persistence for dead-letter replay batches."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def create_batch(conn: sqlite3.Connection, canonical_before: int) -> int:
    cur = conn.execute(
        """
        INSERT INTO replay_batches(
            status, total_count, written_count, closed_count, discarded_count,
            canonical_before, canonical_after, report_json, created_at
        ) VALUES ('running', 0, 0, 0, 0, ?, ?, '{}', datetime('now'))
        """,
        (canonical_before, canonical_before),
    )
    return int(cur.lastrowid)


def finish_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    total: int,
    written: int,
    closed: int,
    discarded: int,
    canonical_before: int,
    canonical_after: int,
    report: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE replay_batches SET
            status='ok', total_count=?, written_count=?, closed_count=?,
            discarded_count=?, canonical_before=?, canonical_after=?,
            report_json=?, finished_at=datetime('now')
        WHERE id=?
        """,
        (
            total,
            written,
            closed,
            discarded,
            canonical_before,
            canonical_after,
            json.dumps(report, ensure_ascii=False),
            batch_id,
        ),
    )


def add_item(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    conflict_id: int,
    business_key: str,
    resolution: str,
    canonical_changed: bool,
    result_note: str,
) -> None:
    conn.execute(
        """
        INSERT INTO replay_batch_items(
            batch_id, conflict_id, business_key, resolution,
            canonical_changed, result_note
        ) VALUES (?,?,?,?,?,?)
        """,
        (batch_id, conflict_id, business_key, resolution, 1 if canonical_changed else 0, result_note),
    )


def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["report"] = json.loads(d.pop("report_json") or "{}")
    return d


def list_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, status, total_count, written_count, closed_count, discarded_count,
               canonical_before, canonical_after, report_json, created_at, finished_at
        FROM replay_batches ORDER BY id DESC
        """
    ).fetchall()
    return [_hydrate(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, status, total_count, written_count, closed_count, discarded_count,
               canonical_before, canonical_after, report_json, created_at, finished_at
        FROM replay_batches WHERE id=?
        """,
        (batch_id,),
    ).fetchone()
    if not row:
        return None
    d = _hydrate(row)
    d["items"] = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, batch_id, conflict_id, business_key, resolution,
                   canonical_changed, result_note
            FROM replay_batch_items WHERE batch_id=? ORDER BY id
            """,
            (batch_id,),
        ).fetchall()
    ]
    return d
