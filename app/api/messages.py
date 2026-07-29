import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.partners import require_internal_auth
from app.dependencies import get_tracker
from app.tracking.models import MessageSummary
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


def _to_response(summary: MessageSummary) -> MessageSummaryResponse:
    return MessageSummaryResponse(
        id=summary.id,
        direction=summary.direction,
        partner_name=summary.partner_name,
        status=summary.status,
        content_digest=summary.content_digest,
        transaction_set=summary.transaction_set,
        trans_id=summary.trans_id,
        received_at=summary.received_at,
        processed_at=summary.processed_at,
    )


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
    return [_to_response(s) for s in summaries]


@router.get("/messages/{message_id}", response_model=MessageSummaryResponse)
async def get_message(
    message_id: uuid.UUID,
    _: None = Depends(require_internal_auth),
    tracker: MessageTracker = Depends(get_tracker),
) -> MessageSummaryResponse:
    """Fetches a single message by id -- e.g. for a downstream consumer that
    already has an id (from a sink payload/filename/object key) and wants
    that message's current status rather than paging through the list
    endpoint."""
    summary = await tracker.get_by_id(message_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="unknown message")
    return _to_response(summary)


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
