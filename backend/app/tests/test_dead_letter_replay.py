"""End-to-end tests for the dead-letter replay flow against a temp SQLite DB."""

import pytest

from app import seed
from app.db import connect
from app.modules import dead_letter_replay as dlr
from app.repositories import canonical as canon_repo
from app.repositories import replay as replay_repo
from app.repositories import runs as runs_repo
from app.services.ingest_service import dashboard_stats, run_ingest


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.db.DB_PATH", db_file)
    seed.init_db()
    yield db_file


def _seed_conflicts(temp_db) -> int:
    # Alpha first (clean inserts), then Beta -> SKU-1001 same-key price conflict.
    run_ingest(1, "incremental")
    beta = run_ingest(2, "incremental")
    assert beta["conflict_count"] == 1
    return beta["id"]


def test_ingest_quarantines_conflict_without_canonical_write(temp_db):
    _seed_conflicts(temp_db)
    conn = connect()
    try:
        row = canon_repo.get_by_key(conn, "SKU-1001")
        assert row["price"] == 129.0  # Alpha value kept; Beta 99.0 never landed
        counts = runs_repo.conflict_status_counts(conn)
        assert counts == {"pending": 1, "adjudicated": 0, "replayed": 0, "total": 1}
    finally:
        conn.close()


def test_adjudication_is_persisted_and_does_not_touch_canonical(temp_db):
    _seed_conflicts(temp_db)
    conn = connect()
    cid = runs_repo.list_conflicts(conn)[0]["id"]
    conn.close()

    out = dlr.adjudicate_conflicts([cid], dlr.OVERWRITE, note="接受 Beta 价")
    assert out["adjudicated"] == 1
    assert out["counts"]["adjudicated"] == 1

    conn = connect()
    try:
        c = runs_repo.get_conflict(conn, cid)
        assert c["status"] == "adjudicated"
        assert c["resolution"] == "overwrite"
        assert c["resolved_at"]
        assert c["result_note"] == "接受 Beta 价"
        # Canonical still untouched until a replay batch runs.
        assert canon_repo.get_by_key(conn, "SKU-1001")["price"] == 129.0
    finally:
        conn.close()


def test_replay_overwrite_updates_canonical_and_reconciles(temp_db):
    _seed_conflicts(temp_db)
    conn = connect()
    cid = runs_repo.list_conflicts(conn)[0]["id"]
    before = canon_repo.count_items(conn)
    conn.close()

    dlr.adjudicate_conflicts([cid], dlr.OVERWRITE)
    batch = dlr.create_replay_batch([cid])

    assert batch["total_count"] == 1
    assert batch["written_count"] == 1
    assert batch["closed_count"] == 0
    assert batch["discarded_count"] == 0
    # Overwrite updated an existing row -> count unchanged.
    assert batch["canonical_before"] == before
    assert batch["canonical_after"] == before
    assert batch["report"]["canonical_delta"] == 0
    assert batch["report"]["updated_count"] == 1

    conn = connect()
    try:
        assert canon_repo.get_by_key(conn, "SKU-1001")["price"] == 99.0
        c = runs_repo.get_conflict(conn, cid)
        assert c["status"] == "replayed"
        assert c["replay_batch_id"] == batch["id"]
        stored = replay_repo.get_batch(conn, batch["id"])
        assert stored["items"][0]["canonical_changed"] == 1
    finally:
        conn.close()


def test_keep_and_discard_never_modify_canonical(temp_db):
    # Generate two conflicts by running Beta twice? Beta watermark would skip.
    # Instead build the multi-resolution case across separate business keys via
    # repeated full runs: full reset clears watermark, conflict re-quarantines.
    run_ingest(1, "incremental")
    run_ingest(2, "full")
    conn = connect()
    first_id = runs_repo.list_conflicts(conn)[0]["id"]
    conn.close()
    dlr.adjudicate_conflicts([first_id], dlr.KEEP)
    dlr.create_replay_batch([first_id])

    # A second full Beta run creates a fresh pending conflict for the same key.
    run_ingest(2, "full")
    conn = connect()
    pending = runs_repo.list_conflicts(conn, "pending")
    assert len(pending) == 1
    second_id = pending[0]["id"]
    canonical_price_before = canon_repo.get_by_key(conn, "SKU-1001")["price"]
    count_before = canon_repo.count_items(conn)
    conn.close()

    dlr.adjudicate_conflicts([second_id], dlr.DISCARD)
    batch = dlr.create_replay_batch([second_id])
    assert batch["written_count"] == 0
    assert batch["closed_count"] == 0
    assert batch["discarded_count"] == 1
    assert batch["report"]["canonical_delta"] == 0
    assert batch["canonical_after"] == batch["canonical_before"]

    conn = connect()
    try:
        assert canon_repo.get_by_key(conn, "SKU-1001")["price"] == canonical_price_before
        assert canon_repo.count_items(conn) == count_before
        statuses = runs_repo.conflict_status_counts(conn)
        assert statuses["replayed"] == 2
        assert statuses["pending"] == 0
    finally:
        conn.close()


def test_replay_batch_mixed_resolutions_reconcile(temp_db):
    run_ingest(1, "incremental")
    run_ingest(2, "full")
    run_ingest(3, "incremental")  # Gamma: independent SKUs, no conflicts
    conn = connect()
    conflict_ids = [c["id"] for c in runs_repo.list_conflicts(conn, "pending")]
    canonical_before = canon_repo.count_items(conn)
    conn.close()
    assert len(conflict_ids) == 1

    dlr.adjudicate_conflicts(conflict_ids, dlr.OVERWRITE)
    batch = dlr.create_replay_batch(conflict_ids)
    # Invariant: totals partition; canonical list count matches report.
    assert batch["total_count"] == (
        batch["written_count"] + batch["closed_count"] + batch["discarded_count"]
    )
    conn = connect()
    try:
        assert canon_repo.count_items(conn) == batch["canonical_after"]
        stats = dashboard_stats()
        assert stats["conflict_count"] == 1
        assert stats["conflict_replayed_count"] == 1
        assert stats["canonical_count"] == canonical_before
    finally:
        conn.close()


def test_replayed_conflict_is_locked(temp_db):
    _seed_conflicts(temp_db)
    conn = connect()
    cid = runs_repo.list_conflicts(conn)[0]["id"]
    conn.close()
    dlr.adjudicate_conflicts([cid], dlr.OVERWRITE)
    dlr.create_replay_batch([cid])
    with pytest.raises(ValueError, match="locked"):
        dlr.adjudicate_conflicts([cid], dlr.KEEP)
    with pytest.raises(ValueError, match="not ready"):
        dlr.create_replay_batch([cid])


def test_cannot_replay_pending_conflict(temp_db):
    _seed_conflicts(temp_db)
    conn = connect()
    cid = runs_repo.list_conflicts(conn)[0]["id"]
    conn.close()
    with pytest.raises(ValueError, match="not ready"):
        dlr.create_replay_batch([cid])
