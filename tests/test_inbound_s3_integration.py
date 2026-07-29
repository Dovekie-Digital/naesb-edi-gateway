"""End-to-end validation of the S3 sink against a real MinIO instance (not
moto's mocked boto3) plus a real Postgres-backed MessageTracker, covering the
full path this PR adds: inbound acceptance -> S3 object keyed by the
message's row id -> GET /api/messages -> POST /api/messages/status.

Exercises `S3Sink`'s `endpoint_url` argument for real (moto intercepts
boto3 regardless of endpoint_url, so tests/test_sinks_s3.py never actually
exercises that code path against real network I/O)."""

import boto3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.postgres import PostgresContainer

from app.api import messages as messages_api
from app.crypto.gpg_wrapper import GpgService
from app.dependencies import (
    get_fingerprints,
    get_gpg,
    get_partners,
    get_settings,
    get_sinks,
    get_tracker,
)
from app.envelope.fields import EnvelopeFields, InputFormat
from app.envelope.multipart_codec import build_multipart_body
from app.envelope.receipt import NaesbReceipt, parse_signed_mime
from app.inbound import routes as inbound_routes
from app.partners import ApiKeyAuthConfig, BasicAuthConfig, PartnerConfig, PartnerRegistry
from app.settings import (
    CryptoConfig,
    EnvelopeConfig,
    IdentityConfig,
    InternalApiConfig,
    ServerConfig,
    Settings,
    SinksConfig,
)
from app.sinks.s3_sink import S3Sink
from app.tracking.db import create_pool, run_migrations
from app.tracking.repository import MessageTracker

pytestmark = pytest.mark.integration

PARTNER_DUNS = "987654321"
PARTNER_NAME = "acme-pipeline"
OUR_DUNS = "123456789"
SERVER_ID = "coolhost.example.com"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
BUCKET = "naesb-inbound-test"


@pytest.fixture(scope="module")
def minio_container():
    container = (
        DockerContainer("minio/minio:latest")
        .with_exposed_ports(9000)
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data")
    )
    with container:
        wait_for_logs(container, "API:")
        yield container


@pytest.fixture(scope="module")
def minio_endpoint(minio_container):
    host = minio_container.get_container_host_ip()
    port = minio_container.get_exposed_port(9000)
    return f"http://{host}:{port}"


@pytest.fixture(scope="module")
def s3_client(minio_endpoint):
    return boto3.client(
        "s3",
        endpoint_url=minio_endpoint,
        region_name="us-east-1",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


@pytest.fixture(scope="module")
def s3_bucket(s3_client):
    s3_client.create_bucket(Bucket=BUCKET)
    return BUCKET


@pytest.fixture
def s3_sink(minio_endpoint, s3_bucket):
    return S3Sink(
        bucket=s3_bucket,
        prefix="inbound/",
        region="us-east-1",
        endpoint_url=minio_endpoint,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
    )


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
def settings(gnupg_home, monkeypatch):
    monkeypatch.setenv("TEST_S3_US_PASSPHRASE", "us-passphrase")
    monkeypatch.setenv("TEST_S3_INTERNAL_API_USERNAME", "admin")
    monkeypatch.setenv("TEST_S3_INTERNAL_API_PASSWORD", "s3cr3t")
    return Settings(
        identity=IdentityConfig(name="MyCompany", duns=OUR_DUNS),
        server=ServerConfig(inbound_path="/inbound", max_body_size_bytes=26_214_400),
        crypto=CryptoConfig(
            private_key_path="unused",
            passphrase_env="TEST_S3_US_PASSPHRASE",
            gnupg_home=gnupg_home,
            cipher_algo="AES256",
            digest_algo="SHA256",
            compress_algo="ZIP",
        ),
        envelope=EnvelopeConfig(server_id=SERVER_ID, default_version="1.9"),
        sinks=SinksConfig(require_at_least_one_durable_success=True),
        internal_api=InternalApiConfig(
            username_env="TEST_S3_INTERNAL_API_USERNAME", password_env="TEST_S3_INTERNAL_API_PASSWORD"
        ),
        partners_file="unused",
    )


@pytest.fixture
def partners(monkeypatch):
    monkeypatch.setenv("TEST_S3_PARTNER_IN_KEY", "partner-inbound-key")
    partner = PartnerConfig(
        name=PARTNER_NAME,
        duns=PARTNER_DUNS,
        endpoint_url="https://partner.example.com/edi/receiver-endpoint",
        pgp_public_key_path="unused",
        outbound_auth=BasicAuthConfig(username="u", password_env="TEST_S3_PARTNER_OUT_PW_UNUSED"),
        inbound_auth=ApiKeyAuthConfig(key_env="TEST_S3_PARTNER_IN_KEY"),
    )
    return PartnerRegistry([partner])


@pytest.fixture
def fingerprints(us_key, partner_key):
    return {"_self": us_key, PARTNER_NAME: partner_key}


def build_client(settings, partners, gpg_service, fingerprints, tracker, sinks) -> TestClient:
    app = FastAPI()
    app.include_router(inbound_routes.router, prefix=settings.server.inbound_path)
    app.include_router(messages_api.router)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_partners] = lambda: partners
    app.dependency_overrides[get_gpg] = lambda: gpg_service
    app.dependency_overrides[get_fingerprints] = lambda: fingerprints
    app.dependency_overrides[get_tracker] = lambda: tracker
    app.dependency_overrides[get_sinks] = lambda: sinks
    return TestClient(app)


def _envelope_fields(**overrides) -> EnvelopeFields:
    defaults = dict(
        from_id=PARTNER_DUNS,
        to_id=OUR_DUNS,
        version="1.9",
        receipt_disposition_to=PARTNER_DUNS,
        input_format=InputFormat.X12,
        receipt_security_selection="signed-receipt-protocol=required,pgp-signature;signed-receipt-micalg=required,sha256",
        transaction_set="NOM00001",
    )
    defaults.update(overrides)
    return EnvelopeFields(**defaults)


def _build_body(
    gpg_service: GpgService, us_key: str, signer_key: str, passphrase: str, payload: bytes = b"ISA*00*..."
) -> tuple[bytes, str]:
    ciphertext = gpg_service.encrypt_and_sign(
        payload, recipient_fingerprint=us_key, signer_fingerprint=signer_key, passphrase=passphrase
    )
    return build_multipart_body(_envelope_fields(), ciphertext)


def _auth_headers() -> dict[str, str]:
    return {"authorization": "Bearer partner-inbound-key"}


def _decode_receipt(gpg_service: GpgService, us_key: str, response) -> NaesbReceipt:
    content_type = response.headers["content-type"]
    report_body, report_content_type, signature = parse_signed_mime(response.content, content_type)
    result = gpg_service.verify_detached(report_body, signature, expected_fingerprint=us_key)
    assert result.valid, "response was not validly signed by our own key"
    return NaesbReceipt.decode_report_part(report_body, report_content_type)


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    import base64

    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"authorization": f"Basic {token}"}


async def test_inbound_message_lands_in_s3_and_is_markable_processed(
    settings, partners, gpg_service, fingerprints, tracker, s3_sink, s3_client, s3_bucket, us_key, partner_key
):
    client = build_client(settings, partners, gpg_service, fingerprints, tracker, [s3_sink])
    body, content_type = _build_body(gpg_service, us_key, partner_key, "partner-passphrase")

    response = client.post("/inbound", headers={**_auth_headers(), "content-type": content_type}, content=body)

    assert response.status_code == 200
    receipt = _decode_receipt(gpg_service, us_key, response)
    assert receipt.is_ok

    # The message is accepted in Postgres (real, not the FakeTracker used by
    # tests/test_inbound_route.py).
    accepted = await tracker.list_by_status("accepted", partner_name=PARTNER_NAME)
    assert len(accepted) == 1
    message_id = accepted[0].id

    # The S3 object (real MinIO, not moto) was written and is keyed by that
    # same message id -- the correlation mechanism this PR introduces.
    listing = s3_client.list_objects_v2(Bucket=s3_bucket, Prefix=f"inbound/{PARTNER_DUNS}/")
    assert listing["KeyCount"] == 1
    key = listing["Contents"][0]["Key"]
    assert str(message_id) in key
    object_body = s3_client.get_object(Bucket=s3_bucket, Key=key)["Body"].read()
    assert object_body == b"ISA*00*..."

    # A downstream consumer discovers it via the list API...
    list_response = client.get(
        "/api/messages",
        params={"status": "accepted", "partner_name": PARTNER_NAME},
        headers=_basic_auth_header("admin", "s3cr3t"),
    )
    assert list_response.status_code == 200
    assert [m["id"] for m in list_response.json()] == [str(message_id)]

    # ...or, having already extracted the id from the S3 key, fetches it
    # directly by id instead of paging through the list endpoint.
    get_response = client.get(
        f"/api/messages/{message_id}", headers=_basic_auth_header("admin", "s3cr3t")
    )
    assert get_response.status_code == 200
    assert get_response.json()["id"] == str(message_id)

    # ...consumes the S3 object (already verified above)...

    # ...and marks it processed via the update API.
    update_response = client.post(
        "/api/messages/status",
        json={"message_ids": [str(message_id)]},
        headers=_basic_auth_header("admin", "s3cr3t"),
    )
    assert update_response.status_code == 200
    assert update_response.json() == {"updated": [str(message_id)], "skipped": []}

    # The transition is durable in Postgres.
    still_accepted = await tracker.list_by_status("accepted", partner_name=PARTNER_NAME)
    assert still_accepted == []
    processed = await tracker.list_by_status("processed", partner_name=PARTNER_NAME)
    assert [m.id for m in processed] == [message_id]
    assert processed[0].processed_at is not None
