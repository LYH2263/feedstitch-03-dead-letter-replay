"""Dead-letter replay: adjudicate quarantined conflicts and replay a batch.

Flow:
1. Ingest keeps same-key / non-key-diverging rows in the conflict queue and never
   writes them to canonical.
2. An operator adjudicates each conflict with one of three resolutions; the
   decision, result note and timestamp are persisted immediately (survives refresh).
3. A replay batch processes a set of adjudicated conflicts. Only ``overwrite``
   resolutions write to canonical; ``keep`` / ``discard`` only close the conflict
   and must never touch canonical data.
"""

from __future__ import annotations

from typing import Any

from app.db import connect
from app.repositories import canonical as canon_repo
from app.repositories import replay as replay_repo
from app.repositories import runs as runs_repo

# 接受传入：以传入侧覆盖写入主数据
OVERWRITE = "overwrite"
# 保留主数据：维持主数据不变并关闭冲突
KEEP = "keep"
# 作废：忽略该冲突且不改主数据
DISCARD = "discard"

RESOLUTIONS = (OVERWRITE, KEEP, DISCARD)

RESOLUTION_LABELS = {
    OVERWRITE: "接受传入·覆盖写入主数据",
    KEEP: "保留主数据·关闭冲突",
    DISCARD: "作废·忽略且不改主数据",
}

# Conflict lifecycle: pending → adjudicated → replayed
PENDING = "pending"
ADJUDICATED = "adjudicated"
REPLAYED = "replayed"


def _load_conflicts(conn, conflict_ids: list[int]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for cid in conflict_ids:
        row = runs_repo.get_conflict(conn, cid)
        if row is None:
            raise ValueError(f"conflict {cid} not found")
        found.append(row)
    return found


def adjudicate_conflicts(
    conflict_ids: list[int],
    resolution: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Persist an adjudication decision on pending/adjudicated conflicts.

    No canonical data is touched at this stage.
    """
    if resolution not in RESOLUTIONS:
        raise ValueError(f"resolution must be one of {', '.join(RESOLUTIONS)}")
    if not conflict_ids:
        raise ValueError("no conflict selected")

    conn = connect()
    try:
        conflicts = _load_conflicts(conn, conflict_ids)
        blocked = [c["id"] for c in conflicts if c["status"] == REPLAYED]
        if blocked:
            raise ValueError(
                f"conflicts already replayed and locked: {', '.join(map(str, blocked))}"
            )

        result_note = (note or "").strip() or RESOLUTION_LABELS[resolution]
        for cid in (c["id"] for c in conflicts):
            conn.execute(
                """
                UPDATE conflicts
                SET status=?, resolution=?, resolved_at=datetime('now'),
                    result_note=?, replay_batch_id=NULL
                WHERE id=?
                """,
                (ADJUDICATED, resolution, result_note, cid),
            )
        conn.commit()
        return {
            "adjudicated": len(conflicts),
            "resolution": resolution,
            "counts": runs_repo.conflict_status_counts(conn),
        }
    finally:
        conn.close()


def _incoming_item(conflict: dict[str, Any]) -> dict[str, Any]:
    payload = conflict["incoming"]
    return {
        "business_key": payload["business_key"],
        "title": payload["title"],
        "price": float(payload["price"]),
        "category": payload["category"],
        "event_ts": payload["event_ts"],
        "source_revision": int(payload["source_revision"]),
        "payload": payload,
    }


def create_replay_batch(conflict_ids: list[int]) -> dict[str, Any]:
    """Replay selected adjudicated conflicts as one auditable batch."""
    if not conflict_ids:
        raise ValueError("no conflict selected")

    conn = connect()
    try:
        conflicts = _load_conflicts(conn, conflict_ids)
        not_ready = [c["id"] for c in conflicts if c["status"] != ADJUDICATED]
        if not_ready:
            raise ValueError(
                "only adjudicated conflicts can be replayed; "
                f"not ready: {', '.join(map(str, not_ready))}"
            )

        canonical_before = canon_repo.count_items(conn)
        batch_id = replay_repo.create_batch(conn, canonical_before)

        written = closed = discarded = 0
        inserted = updated = 0
        item_results: list[dict[str, Any]] = []

        for conflict in conflicts:
            resolution = conflict["resolution"]
            key = conflict["business_key"]
            canonical_changed = False

            if resolution == OVERWRITE:
                item = _incoming_item(conflict)
                existed = canon_repo.get_by_key(conn, key) is not None
                canon_repo.upsert_item(conn, item, conflict["source_id"])
                canonical_changed = True
                written += 1
                if existed:
                    updated += 1
                    action = "canonical_updated"
                else:
                    inserted += 1
                    action = "canonical_inserted"
                note = f"覆盖写入主数据（{action}）"
            elif resolution == KEEP:
                closed += 1
                action = "closed_kept_canonical"
                note = "保留主数据，冲突关闭，主数据未改动"
            else:  # DISCARD
                discarded += 1
                action = "discarded_ignored"
                note = "作废忽略，主数据未改动"

            conn.execute(
                """
                UPDATE conflicts
                SET status=?, replay_batch_id=?, result_note=?
                WHERE id=?
                """,
                (REPLAYED, batch_id, note, conflict["id"]),
            )
            replay_repo.add_item(
                conn,
                batch_id=batch_id,
                conflict_id=conflict["id"],
                business_key=key,
                resolution=resolution,
                canonical_changed=canonical_changed,
                result_note=note,
            )
            item_results.append(
                {
                    "conflict_id": conflict["id"],
                    "business_key": key,
                    "resolution": resolution,
                    "action": action,
                    "canonical_changed": canonical_changed,
                    "result_note": note,
                }
            )

        total = len(conflicts)
        canonical_after = canon_repo.count_items(conn)
        report = {
            "batch_id": batch_id,
            "total_count": total,
            "written_count": written,
            "closed_count": closed,
            "discarded_count": discarded,
            "inserted_count": inserted,
            "updated_count": updated,
            "canonical_before": canonical_before,
            "canonical_after": canonical_after,
            "canonical_delta": canonical_after - canonical_before,
            "reconciliation": (
                "total = written + closed + discarded; "
                "canonical_delta = inserted（覆盖更新不改条数）；"
                "仅 overwrite 会写主数据"
            ),
            "items": item_results,
        }
        replay_repo.finish_batch(
            conn,
            batch_id,
            total=total,
            written=written,
            closed=closed,
            discarded=discarded,
            canonical_before=canonical_before,
            canonical_after=canonical_after,
            report=report,
        )
        conn.commit()
        return replay_repo.get_batch(conn, batch_id)
    finally:
        conn.close()
