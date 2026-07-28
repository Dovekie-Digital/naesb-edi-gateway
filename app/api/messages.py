import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.partners import require_internal_auth
from app.dependencies import get_tracker
from app.tracking.repository import RESERVED_STATUSES, MessageTracker

router = APIRouter(prefix="/api", tags=["messages"])


class MessageSummaryResponse(BaseModel):
    id: uuid.UUID
    direction: str
    partner_name: str
    status: str
    content_digest: str
    transaction_set: str | None = None
    trans_id: int | None = None
    received_at: datetime | None = None
    processed_at: datetime | None = None


class UpdateStatusRequest(BaseModel):
    message_ids: list[uuid.UUID]
    status: str = "processed"


class UpdateStatusResponse(BaseModel):
    updated: list[uuid.UUID]
    skipped: list[uuid.UUID]


@router.get("/messages", response_model=list[MessageSummaryResponse])
async def list_messages(
    status: str,
    partner_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _: None = Depends(require_internal_auth),
    tracker: MessageTracker = Depends(get_tracker),
) -> list[MessageSummaryResponse]:
    """Lets a downstream consumer discover messages by status (e.g.
    `?status=accepted`) so it knows what still needs to be processed."""
    summaries = await tracker.list_by_status(
        status, partner_name=partner_name, limit=limit, offset=offset
    )
    return [
        MessageSummaryResponse(
            id=s.id,
            direction=s.direction,
            partner_name=s.partner_name,
            status=s.status,
            content_digest=s.content_digest,
            transaction_set=s.transaction_set,
            trans_id=s.trans_id,
            received_at=s.received_at,
            processed_at=s.processed_at,
        )
        for s in summaries
    ]


@router.post("/messages/status", response_model=UpdateStatusResponse)
async def update_message_status(
    body: UpdateStatusRequest,
    _: None = Depends(require_internal_auth),
    tracker: MessageTracker = Depends(get_tracker),
) -> UpdateStatusResponse:
    """Lets a downstream consumer mark one or more accepted messages as
    processed (or another caller-defined terminal status), once it has
    finished consuming the delivered content. Only messages currently
    'accepted' are eligible -- anything else comes back in `skipped` rather
    than raising, since a batch call may legitimately mix already-processed
    and not-yet-accepted ids."""
    if body.status in RESERVED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status {body.status!r} is reserved for the gateway's own pipeline",
        )
    result = await tracker.mark_processed(body.message_ids, status=body.status)
    return UpdateStatusResponse(updated=result.updated, skipped=result.skipped)
