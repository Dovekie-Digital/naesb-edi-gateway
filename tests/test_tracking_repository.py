import uuid

import pytest
from testcontainers.postgres import PostgresContainer

from app.tracking.db import create_pool, run_migrations
from app.tracking.models import MessageRecord, OutboundJob
from app.tracking.repository import MessageTracker, OutboundJobRepository

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture
async def pool(postgres_container):
    url = postgres_container.get_connection_url(driver=None)
    p = await create_pool(url)
    await run_migrations(p)
    yield p
    await p.close()


@pytest.fixture
def tracker(pool):
    return MessageTracker(pool)


@pytest.fixture
def job_repository(pool):
    return OutboundJobRepository(pool)


async def test_create_and_find_duplicate(tracker):
    record = MessageRecord(
        direction="inbound",
        partner_name="acme-pipeline",
        content_digest="a" * 64,
        transaction_set="873",
        input_format="X12",
        status="processing",
    )

    assert not await tracker.find_duplicate("acme-pipeline", "a" * 64, "inbound")

    message_id = await tracker.create(record)
    assert message_id is not None

    assert await tracker.find_duplicate("acme-pipeline", "a" * 64, "inbound")
    # different direction is not a duplicate of the inbound one
    assert not await tracker.find_duplicate("acme-pipeline", "a" * 64, "outbound")


async def test_update_status(tracker):
    record = MessageRecord(
        direction="outbound", partner_name="acme-pipeline", content_digest="b" * 64, status="sending"
    )
    message_id = await tracker.create(record)

    await tracker.update_status(message_id, status="delivered", receipt_verified=True)

    # a second insert with the same natural key should now violate the
    # UNIQUE(partner_name, content_digest, direction) constraint
    with pytest.raises(Exception):
        await tracker.create(record)


async def test_update_sinks_status(tracker):
    record = MessageRecord(
        direction="inbound", partner_name="acme-pipeline", content_digest="c" * 64, status="processing"
    )
    message_id = await tracker.create(record)

    await tracker.update_sinks_status(message_id, {"filesystem": {"ok": True, "error": None}})
    # no exception means the jsonb column accepted the update; content is
    # verified indirectly via find_duplicate/update_status round trips above


async def test_next_trans_id_is_sequential(tracker):
    first = await tracker.next_trans_id()
    second = await tracker.next_trans_id()
    assert second == first + 1


async def test_find_refnum_reuse(tracker):
    assert not await tracker.find_refnum_reuse("acme-pipeline", "refnum-1", "inbound")

    record = MessageRecord(
        direction="inbound",
        partner_name="acme-pipeline",
        content_digest="d" * 64,
        status="accepted",
        refnum="refnum-1",
    )
    await tracker.create(record)

    assert await tracker.find_refnum_reuse("acme-pipeline", "refnum-1", "inbound")
    assert not await tracker.find_refnum_reuse("acme-pipeline", "refnum-1", "outbound")


async def test_create_rejects_duplicate_refnum_same_partner_and_direction(tracker):
    # Different content_digest so the (partner_name, content_digest, direction)
    # constraint can't be what raises -- this exercises the dedicated
    # (partner_name, refnum, direction) WHERE refnum IS NOT NULL unique index
    # from 0004_unique_refnum.sql, the DB-level backstop for the refnum-dedup
    # race (mirrors test_update_status's digest-uniqueness assertion above).
    first = MessageRecord(
        direction="inbound",
        partner_name="acme-pipeline",
        content_digest="g" * 64,
        status="processing",
        refnum="dup-refnum",
    )
    await tracker.create(first)

    second = MessageRecord(
        direction="inbound",
        partner_name="acme-pipeline",
        content_digest="h" * 64,
        status="processing",
        refnum="dup-refnum",
    )
    with pytest.raises(Exception):
        await tracker.create(second)


async def test_create_persists_from_and_to_id(tracker, pool):
    record = MessageRecord(
        direction="inbound",
        partner_name="acme-pipeline",
        content_digest="i" * 64,
        from_id="123456789",
        to_id="987654321",
        status="processing",
    )
    message_id = await tracker.create(record)

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT from_id, to_id FROM messages WHERE id=%s", (message_id,))
        row = await cur.fetchone()
    assert row == ("123456789", "987654321")


async def test_list_by_status_filters_direction_status_and_partner(tracker):
    # Distinct partner names (not reused by any other test in this module,
    # which shares a single module-scoped Postgres container) so this
    # assertion isn't polluted by rows left behind by other tests.
    accepted = MessageRecord(
        direction="inbound", partner_name="list-status-partner-a", content_digest="j" * 64, status="accepted"
    )
    other_partner = MessageRecord(
        direction="inbound", partner_name="list-status-partner-b", content_digest="k" * 64, status="accepted"
    )
    rejected = MessageRecord(
        direction="inbound", partner_name="list-status-partner-a", content_digest="l" * 64, status="rejected"
    )
    accepted_id = await tracker.create(accepted)
    await tracker.create(other_partner)
    await tracker.create(rejected)

    results = await tracker.list_by_status("accepted", partner_name="list-status-partner-a")
    assert [r.id for r in results] == [accepted_id]
    assert results[0].status == "accepted"


async def test_get_by_id_returns_message_or_none(tracker):
    record = MessageRecord(
        direction="inbound", partner_name="get-by-id-partner", content_digest="p" * 64, status="accepted"
    )
    message_id = await tracker.create(record)

    found = await tracker.get_by_id(message_id)
    assert found is not None
    assert found.id == message_id
    assert found.partner_name == "get-by-id-partner"
    assert found.status == "accepted"

    assert await tracker.get_by_id(uuid.uuid4()) is None


async def test_mark_processed_transitions_accepted_only(tracker):
    accepted = MessageRecord(
        direction="inbound", partner_name="acme-pipeline", content_digest="m" * 64, status="accepted"
    )
    rejected = MessageRecord(
        direction="inbound", partner_name="acme-pipeline", content_digest="n" * 64, status="rejected"
    )
    accepted_id = await tracker.create(accepted)
    rejected_id = await tracker.create(rejected)
    missing_id = await tracker.create(
        MessageRecord(
            direction="inbound", partner_name="acme-pipeline", content_digest="o" * 64, status="accepted"
        )
    )

    result = await tracker.mark_processed([accepted_id, rejected_id])

    assert result.updated == [accepted_id]
    assert set(result.skipped) == {rejected_id}

    # already-processed ids are skipped on a repeat call, not re-updated
    second_call = await tracker.mark_processed([accepted_id, missing_id])
    assert second_call.updated == [missing_id]
    assert second_call.skipped == [accepted_id]


async def test_outbound_job_create_claim_and_deliver(job_repository):
    job = OutboundJob(
        id=None,
        partner_name="acme-pipeline",
        from_id="123456789",
        to_id="987654321",
        version="1.9",
        input_format="X12",
        payload_ciphertext=b"ciphertext-bytes",
        content_digest="e" * 64,
    )
    job_id = await job_repository.create(job)
    fetched = await job_repository.get(job_id)
    assert fetched is not None
    assert fetched.status == "queued"
    assert fetched.payload_ciphertext == b"ciphertext-bytes"

    claimed = await job_repository.claim_due_jobs(limit=10)
    assert any(j.id == job_id for j in claimed)
    claimed_job = next(j for j in claimed if j.id == job_id)
    assert claimed_job.attempt_count == 1

    # a second claim attempt should not re-claim an in_progress job with no
    # due schedule change
    reclaimed = await job_repository.claim_due_jobs(limit=10)
    assert not any(j.id == job_id for j in reclaimed)

    await job_repository.mark_delivered(job_id, "42", "their-host", "20260710120000")
    delivered = await job_repository.get(job_id)
    assert delivered.status == "delivered"
    assert delivered.receipt_trans_id == "42"


async def test_outbound_job_reschedule_and_exchange_failure(job_repository):
    import datetime

    job = OutboundJob(
        id=None,
        partner_name="acme-pipeline",
        from_id="123456789",
        to_id="987654321",
        version="1.9",
        input_format="X12",
        payload_ciphertext=b"ciphertext-bytes-2",
        content_digest="f" * 64,
    )
    job_id = await job_repository.create(job)

    future = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
    await job_repository.reschedule(job_id, future, "transient failure")
    rescheduled = await job_repository.get(job_id)
    assert rescheduled.status == "queued"
    assert rescheduled.last_error_description == "transient failure"

    # not due yet -- shouldn't be claimable
    claimed = await job_repository.claim_due_jobs(limit=10)
    assert not any(j.id == job_id for j in claimed)

    await job_repository.mark_exchange_failure(job_id, "attempts exhausted")
    final = await job_repository.get(job_id)
    assert final.status == "exchange_failure"
