import pytest

from app.crypto.keyring import KeyringError, bootstrap_keyring
from app.partners import ApiKeyAuthConfig, CryptoOverrides, PartnerConfig


def _make_partner(name: str, key_path: str, min_rsa_key_bits: int | None = None) -> PartnerConfig:
    return PartnerConfig(
        name=name,
        duns="000000000",
        endpoint_url="https://example.invalid/receiver",
        pgp_public_key_path=key_path,
        outbound_auth=ApiKeyAuthConfig(key_env="UNUSED_OUTBOUND_KEY"),
        inbound_auth=ApiKeyAuthConfig(key_env="UNUSED_INBOUND_KEY"),
        crypto_overrides=(
            CryptoOverrides(min_rsa_key_bits=min_rsa_key_bits) if min_rsa_key_bits is not None else None
        ),
    )


@pytest.fixture(scope="module")
def weak_partner_key(raw_gpg):
    """A legacy 1024-bit RSA key, standing in for a real trading partner's
    on-file key that predates the 2048-bit floor (WGQ Cybersecurity Related
    Standards v4.0, Appendix A)."""
    key_input = raw_gpg.gen_key_input(
        key_type="RSA",
        key_length=1024,
        name_real="legacy-partner",
        name_email="legacy-partner@example.com",
        passphrase="legacy-partner-passphrase",
    )
    key = raw_gpg.gen_key(key_input)
    assert key.fingerprint, f"key generation failed: {key.status} / {key.stderr}"
    return key.fingerprint


@pytest.fixture
def our_private_key_path(tmp_path, raw_gpg, us_key):
    armored = raw_gpg.export_keys(us_key, secret=True, passphrase="us-passphrase")
    path = tmp_path / "our_private_key.asc"
    path.write_text(armored)
    return str(path)


@pytest.fixture
def weak_partner_public_key_path(tmp_path, raw_gpg, weak_partner_key):
    armored = raw_gpg.export_keys(weak_partner_key)
    path = tmp_path / "legacy-partner.pub.asc"
    path.write_text(armored)
    return str(path)


def test_bootstrap_keyring_rejects_partner_key_below_global_floor(
    raw_gpg, our_private_key_path, weak_partner_public_key_path
):
    partner = _make_partner("legacy-partner", weak_partner_public_key_path)

    with pytest.raises(KeyringError, match="below minimum"):
        bootstrap_keyring(raw_gpg, our_private_key_path, [partner], min_bits=2048, recommended_bits=4096)


def test_bootstrap_keyring_allows_partner_key_below_floor_with_override(
    raw_gpg, our_private_key_path, weak_partner_public_key_path
):
    partner = _make_partner("legacy-partner", weak_partner_public_key_path, min_rsa_key_bits=1024)

    fingerprints = bootstrap_keyring(
        raw_gpg, our_private_key_path, [partner], min_bits=2048, recommended_bits=4096
    )

    assert fingerprints["legacy-partner"]


def test_bootstrap_keyring_override_does_not_weaken_floor_for_other_partners(
    raw_gpg, our_private_key_path, weak_partner_public_key_path
):
    """A crypto_overrides.min_rsa_key_bits on one partner must not affect the
    floor applied to any other key in the keyring, including our own."""
    lenient_partner = _make_partner("legacy-partner", weak_partner_public_key_path, min_rsa_key_bits=1024)

    # us_key is a real 2048-bit key (see conftest.keypair); asking for a
    # 4096-bit floor with no override for "_self" must still fail even
    # though legacy-partner's override lets *its* weak key through.
    with pytest.raises(KeyringError, match="below minimum"):
        bootstrap_keyring(raw_gpg, our_private_key_path, [lenient_partner], min_bits=4096, recommended_bits=4096)
