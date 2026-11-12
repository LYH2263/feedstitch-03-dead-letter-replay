from app.db import connect


# Columns added to the original conflicts table for the dead-letter replay flow.
CONFLICT_ADDED_COLUMNS = {
    "status": "TEXT NOT NULL DEFAULT 'pending'",
    "resolution": "TEXT",
    "resolved_at": "TEXT",
    "result_note": "TEXT",
    "replay_batch_id": "INTEGER",
}


def _migrate_conflicts(conn) -> None:
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(conflicts)").fetchall()}
    for name, ddl in CONFLICT_ADDED_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE conflicts ADD COLUMN {name} {ddl}")


def init_db():
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS sources(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            format TEXT NOT NULL,
            fixture_path TEXT NOT NULL,
            watermark_ts TEXT,
            lookback_seconds INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            parsed_count INTEGER NOT NULL,
            upserted_count INTEGER NOT NULL,
            conflict_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staging_rows(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            event_ts TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            disposition TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS canonical_items(
            id INTEGER PRIMARY KEY,
            business_key TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            event_ts TEXT NOT NULL,
            source_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conflicts(
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            existing_payload TEXT NOT NULL,
            incoming_payload TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            resolution TEXT,
            resolved_at TEXT,
            result_note TEXT,
            replay_batch_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS replay_batches(
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            total_count INTEGER NOT NULL,
            written_count INTEGER NOT NULL,
            closed_count INTEGER NOT NULL,
            discarded_count INTEGER NOT NULL,
            canonical_before INTEGER NOT NULL,
            canonical_after INTEGER NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS replay_batch_items(
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            conflict_id INTEGER NOT NULL,
            business_key TEXT NOT NULL,
            resolution TEXT NOT NULL,
            canonical_changed INTEGER NOT NULL,
            result_note TEXT NOT NULL
        );
        """
    )
    _migrate_conflicts(conn)
    if conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"] == 0:
        conn.executemany(
            """
            INSERT INTO sources(name, format, fixture_path, watermark_ts, lookback_seconds, enabled, note)
            VALUES (?,?,?,?,?,?,?)
            """,
            [
                (
                    "Alpha Catalog",
                    "json",
                    "alpha_catalog.json",
                    None,
                    0,
                    1,
                    "clean：三件独立货号，首跑应全部 upsert",
                ),
                (
                    "Beta Offers",
                    "jsonl",
                    "beta_offers.jsonl",
                    None,
                    0,
                    1,
                    "dirty：SKU-1001 与 Alpha 同键异价→冲突；另含独立货号",
                ),
                (
                    "Gamma Shelf",
                    "xml",
                    "gamma_shelf.xml",
                    None,
                    0,
                    1,
                    "xml 源：两件独立货号",
                ),
            ],
        )
        conn.execute("INSERT INTO settings(key, value) VALUES ('project_label', 'Feedstitch')")
        conn.execute("INSERT INTO settings(key, value) VALUES ('default_mode', 'incremental')")
        conn.commit()
    conn.close()
