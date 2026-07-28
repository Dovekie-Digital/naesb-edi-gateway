import uuid

from app.envelope.fields import InputFormat
from app.outbound.enqueue import enqueue_outbound
from app.partners import ApiKeyAuthConfig, BasicAuthConfig, PartnerConfig
from app.settings import (
    CryptoConfig,
    EnvelopeConfig,
    IdentityConfig,
    InternalApiConfig,
    Settings,
)
from app.tracking.models import MessageRecord, OutboundJob


class FakeGpgService:
    def encrypt_and_sign(self, data, recipient_fingerprint, signer_fingerprint, passphrase) -> bytes:
        return b"ciphertext-" + data


class FakeMessageTracker:
    def __init__(self):
        self.records: list[MessageRecord] = []

    async def create(self, record: MessageRecord) -> uuid.UUID:
        self.records.append(record)
        return uuid.uuid4()


class FakeJobRepository:
    def __init__(self):
        self.jobs: list[OutboundJob] = []

    async def create(self, job: OutboundJob) -> uuid.UUID:
        self.jobs.append(job)
        return uuid.uuid4()


def _settings(monkeypatch) -> Settings:
    monkeypatch.setenv("TEST_ENQUEUE_UNUSED_PASSPHRASE", "unused")
    return Settings(
        identity=IdentityConfig(name="MyCompany", duns="123456789"),
        crypto=CryptoConfig(
            private_key_path="unused",
            passphrase_env="TEST_ENQUEUE_UNUSED_PASSPHRASE",
            gnupg_home="/tmp/unused",
        ),
        envelope=EnvelopeConfig(server_id="gateway.example.com", default_version="1.9"),
        internal_api=InternalApiConfig(
            username_env="TEST_ENQUEUE_UNUSED_USERNAME", password_env="TEST_ENQUEUE_UNUSED_PASSWORD"
        ),
        partners_file="unused",
    )


def _partner() -> PartnerConfig:
    return PartnerConfig(
        name="acme-pipeline",
        duns="987654321",
        endpoint_url="https://acme.example.com/edi/receiver-endpoint",
        pgp_public_key_path="unused",
        outbound_auth=BasicAuthConfig(username="u", password_env="TEST_ENQUEUE_UNUSED_OUT_PW"),
        inbound_auth=ApiKeyAuthConfig(key_env="TEST_ENQUEUE_UNUSED_IN_KEY"),
    )


async def test_enqueue_outbound_persists_from_and_to_id(monkeypatch):
    settings = _settings(monkeypatch)
    partner = _partner()
    tracker = FakeMessageTracker()
    jobs = FakeJobRepository()

    await enqueue_outbound(
        b"payload",
        partner=partner,
        input_format=InputFormat.X12,
        transaction_set="873",
        refnum=None,
        refnum_orig=None,
        settings=settings,
        gpg=FakeGpgService(),
        fingerprints={"acme-pipeline": "fingerprint", "_self": "self-fingerprint"},
        tracker=tracker,
        jobs=jobs,
    )

    (record,) = tracker.records
    assert record.from_id == "123456789"
    assert record.to_id == "987654321"
