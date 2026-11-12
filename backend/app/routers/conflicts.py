from fastapi import APIRouter, Query

from app.db import connect
from app.repositories import runs as runs_repo

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts(status: str | None = Query(default=None)):
    conn = connect()
    try:
        return runs_repo.list_conflicts(conn, status)
    finally:
        conn.close()
