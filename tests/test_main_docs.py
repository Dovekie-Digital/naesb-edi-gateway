import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_settings
from app.main import register_protected_docs
from app.settings import CryptoConfig, EnvelopeConfig, IdentityConfig, InternalApiConfig, Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("TEST_DOCS_INTERNAL_API_USERNAME", "admin")
    monkeypatch.setenv("TEST_DOCS_INTERNAL_API_PASSWORD", "s3cr3t")
    monkeypatch.setenv("TEST_DOCS_UNUSED_PASSPHRASE", "unused")
    return Settings(
        identity=IdentityConfig(name="MyCompany", duns="123456789"),
        crypto=CryptoConfig(
            private_key_path="unused",
            passphrase_env="TEST_DOCS_UNUSED_PASSPHRASE",
            gnupg_home="/tmp/unused",
        ),
        envelope=EnvelopeConfig(server_id="gateway.example.com", default_version="1.9"),
        internal_api=InternalApiConfig(
            username_env="TEST_DOCS_INTERNAL_API_USERNAME", password_env="TEST_DOCS_INTERNAL_API_PASSWORD"
        ),
        partners_file="unused",
    )


def build_client(settings) -> TestClient:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.dependency_overrides[get_settings] = lambda: settings
    register_protected_docs(app)
    return TestClient(app)


def _basic_auth_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"authorization": f"Basic {token}"}


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_docs_endpoints_require_auth(settings, path):
    client = build_client(settings)
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_docs_endpoints_reject_wrong_credentials(settings, path):
    client = build_client(settings)
    response = client.get(path, headers=_basic_auth_header("admin", "wrong"))
    assert response.status_code == 401


def test_openapi_json_returns_spec_when_authenticated(settings):
    client = build_client(settings)
    response = client.get("/openapi.json", headers=_basic_auth_header("admin", "s3cr3t"))
    assert response.status_code == 200
    body = response.json()
    assert "openapi" in body
    assert "paths" in body


def test_swagger_ui_served_when_authenticated(settings):
    client = build_client(settings)
    response = client.get("/docs", headers=_basic_auth_header("admin", "s3cr3t"))
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "swagger-ui" in response.text.lower()


def test_redoc_served_when_authenticated(settings):
    client = build_client(settings)
    response = client.get("/redoc", headers=_basic_auth_header("admin", "s3cr3t"))
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "redoc" in response.text.lower()
