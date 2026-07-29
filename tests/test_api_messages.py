import base64
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import messages as messages_api
from app.dependencies import get_settings, get_tracker
from app.settings import CryptoConfig, EnvelopeConfig, IdentityConfig, InternalApiConfig, Settings
from app.tracking.models import MessageSummary
from app.tracking.repository import MarkProcessedResult


class FakeTracker:
    def __init__(self):
        self.list_calls = []
        self.mark_processed_calls = []
        self.summaries: list[MessageSummary] = []
        self.by_id: dict[uuid.UUID, MessageSummary] = {}
        self.mark_processed_result = MarkProcessedResult(updated=[], skipped=[])

    async def list_by_status(self, status, *, direction="inbound", partner_name=None, limit=100, offset=0):
        self.list_calls.append(
            dict(status=status, direction=direction, partner_name=partner_name, limit=limit, offset=offset)
        )
        return self.summaries

    async def get_by_id(self, message_id):
        return self.by_id.get(message_id)

    async def mark_processed(self, message_ids, *, status="processed"):
        self.mark_processed_calls.append(dict(message_ids=message_ids, status=status))
        return self.mark_processed_result


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("TEST_MSG_INTERNAL_API_USERNAME", "admin")
    monkeypatch.setenv("TEST_MSG_INTERNAL_API_PASSWORD", "s3cr3t")
    monkeypatch.setenv("TEST_MSG_UNUSED_PASSPHRASE", "unused")
    return Settings(
        identity=IdentityConfig(name="MyCompany", duns="123456789"),
        crypto=CryptoConfig(
            private_key_path="unused",
            passphrase_env="TEST_MSG_UNUSED_PASSPHRASE",
            gnupg_home="/tmp/unused",
        ),
        envelope=EnvelopeConfig(server_id="gateway.example.com", default_version="1.9"),
        internal_api=InternalApiConfig(
            username_env="TEST_MSG_INTERNAL_API_USERNAME", password_env="TEST_MSG_INTERNAL_API_PASSWORD"
        ),
        partners_file="unused",
    )


@pytest.fixture
def tracker():
    return FakeTracker()


def build_client(settings, tracker) -> TestClient:
    app = FastAPI()
    app.include_router(messages_api.router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_tracker] = lambda: tracker
    return TestClient(app)


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"authorization": f"Basic {token}"}


def test_list_messages_requires_auth(settings, tracker):
    client = build_client(settings, tracker)
    response = client.get("/api/messages", params={"status": "accepted"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_list_messages_rejects_wrong_credentials(settings, tracker):
    client = build_client(settings, tracker)
    response = client.get(
        "/api/messages", params={"status": "accepted"}, headers=_basic_auth_header("admin", "wrong")
    )
    assert response.status_code == 401


def test_list_messages_returns_summaries(settings, tracker):
    message_id = uuid.uuid4()
    tracker.summaries = [
        MessageSummary(
            id=message_id,
            direction="inbound",
            partner_name="acme-pipeline",
            status="accepted",
            content_digest="a" * 64,
            transaction_set="NOM00001",
            trans_id=1,
            received_at=datetime(2026, 7, 8, 19, 30, 0, tzinfo=UTC),
            processed_at=None,
        )
    ]
    client = build_client(settings, tracker)

    response = client.get(
        "/api/messages",
        params={"status": "accepted", "partner_name": "acme-pipeline"},
        headers=_basic_auth_header("admin", "s3cr3t"),
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(message_id)
    assert body[0]["status"] == "accepted"
    assert tracker.list_calls == [
        dict(status="accepted", direction="inbound", partner_name="acme-pipeline", limit=100, offset=0)
    ]


def test_update_message_status_requires_auth(settings, tracker):
    client = build_client(settings, tracker)
    response = client.post("/api/messages/status", json={"message_ids": [str(uuid.uuid4())]})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_update_message_status_returns_updated_and_skipped(settings, tracker):
    updated_id = uuid.uuid4()
    skipped_id = uuid.uuid4()
    tracker.mark_processed_result = MarkProcessedResult(updated=[updated_id], skipped=[skipped_id])
    client = build_client(settings, tracker)

    response = client.post(
        "/api/messages/status",
        json={"message_ids": [str(updated_id), str(skipped_id)], "status": "processed"},
        headers=_basic_auth_header("admin", "s3cr3t"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == [str(updated_id)]
    assert body["skipped"] == [str(skipped_id)]
    assert tracker.mark_processed_calls == [
        dict(message_ids=[updated_id, skipped_id], status="processed")
    ]


def test_update_message_status_rejects_reserved_status(settings, tracker):
    client = build_client(settings, tracker)

    response = client.post(
        "/api/messages/status",
        json={"message_ids": [str(uuid.uuid4())], "status": "accepted"},
        headers=_basic_auth_header("admin", "s3cr3t"),
    )

    assert response.status_code == 400
    assert not tracker.mark_processed_calls


def test_get_message_requires_auth(settings, tracker):
    client = build_client(settings, tracker)
    response = client.get(f"/api/messages/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


def test_get_message_rejects_wrong_credentials(settings, tracker):
    client = build_client(settings, tracker)
    response = client.get(
        f"/api/messages/{uuid.uuid4()}", headers=_basic_auth_header("admin", "wrong")
    )
    assert response.status_code == 401


def test_get_message_returns_404_when_unknown(settings, tracker):
    client = build_client(settings, tracker)
    response = client.get(
        f"/api/messages/{uuid.uuid4()}", headers=_basic_auth_header("admin", "s3cr3t")
    )
    assert response.status_code == 404


def test_get_message_returns_summary_when_found(settings, tracker):
    message_id = uuid.uuid4()
    tracker.by_id[message_id] = MessageSummary(
        id=message_id,
        direction="inbound",
        partner_name="acme-pipeline",
        status="accepted",
        content_digest="a" * 64,
        transaction_set="NOM00001",
        trans_id=1,
        received_at=datetime(2026, 7, 8, 19, 30, 0, tzinfo=UTC),
        processed_at=None,
    )
    client = build_client(settings, tracker)

    response = client.get(
        f"/api/messages/{message_id}", headers=_basic_auth_header("admin", "s3cr3t")
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(message_id)
    assert body["status"] == "accepted"
    assert body["partner_name"] == "acme-pipeline"
