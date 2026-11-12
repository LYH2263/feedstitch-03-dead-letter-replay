from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules import dead_letter_replay as dlr
from app.repositories import replay as replay_repo
from app.repositories import runs as runs_repo
from app.schemas.entities import AdjudicationRequest, ReplayRequest

router = APIRouter(tags=["replay"])


@router.get("/conflicts/counts")
def conflict_counts():
    conn = connect()
    try:
        return runs_repo.conflict_status_counts(conn)
    finally:
        conn.close()


@router.post("/conflicts/adjudicate")
def adjudicate(body: AdjudicationRequest):
    try:
        return dlr.adjudicate_conflicts(body.conflict_ids, body.resolution, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/replay-batches")
def create_replay_batch(body: ReplayRequest):
    try:
        return dlr.create_replay_batch(body.conflict_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/replay-batches")
def list_replay_batches():
    conn = connect()
    try:
        return replay_repo.list_batches(conn)
    finally:
        conn.close()


@router.get("/replay-batches/{batch_id}")
def get_replay_batch(batch_id: int):
    conn = connect()
    try:
        batch = replay_repo.get_batch(conn, batch_id)
        if not batch:
            raise HTTPException(404, "replay batch not found")
        return batch
    finally:
        conn.close()
