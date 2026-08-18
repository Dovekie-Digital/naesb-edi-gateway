import pytest

from app.crypto.gpg_wrapper import GpgError
from app.crypto.policy import WeakAlgorithmError, check_key_length, enforce_policy


def test_encrypt_and_decrypt_roundtrip(gpg_service, us_key, partner_key):
    plaintext = b"NAESB test payload -- 873 nomination content"
    encrypted = gpg_service.encrypt_and_sign(
        plaintext,
        recipient_fingerprint=us_key,
        signer_fingerprint=partner_key,
        passphrase="partner-passphrase",
    )
    assert encrypted != plaintext

    result = gpg_service.decrypt_and_verify(encrypted, passphrase="us-passphrase")
    assert result.ok
    assert result.signature_valid
    assert result.signer_fingerprint == partner_key
    assert result.plaintext == plaintext


def test_decrypt_and_verify_reports_modern_algorithms(gpg_service, us_key, partner_key):
    encrypted = gpg_service.encrypt_and_sign(
        b"payload",
        recipient_fingerprint=us_key,
        signer_fingerprint=partner_key,
        passphrase="partner-passphrase",
    )
    result = gpg_service.decrypt_and_verify(encrypted, passphrase="us-passphrase")

    # Assert against the real roundtrip, not a hardcoded status-line string --
    # if a future GnuPG version changes DECRYPTION_INFO/VALIDSIG field order,
    # this must fail loudly rather than silently accept weak crypto.
    enforce_policy(result.algo_info, allowed_ciphers={"AES256"}, allowed_digests={"SHA256"})


def test_enforce_policy_rejects_weak_cipher(gpg_service, us_key, partner_key):
    encrypted = gpg_service.encrypt_and_sign(
        b"payload",
        recipient_fingerprint=us_key,
        signer_fingerprint=partner_key,
        passphrase="partner-passphrase",
    )
    result = gpg_service.decrypt_and_verify(encrypted, passphrase="us-passphrase")

    with pytest.raises(WeakAlgorithmError):
        enforce_policy(result.algo_info, allowed_ciphers={"3DES"}, allowed_digests={"SHA256"})


def test_enforce_policy_rejects_weak_digest(gpg_service, us_key, partner_key):
    encrypted = gpg_service.encrypt_and_sign(
        b"payload",
        recipient_fingerprint=us_key,
        signer_fingerprint=partner_key,
        passphrase="partner-passphrase",
    )
    result = gpg_service.decrypt_and_verify(encrypted, passphrase="us-passphrase")

    with pytest.raises(WeakAlgorithmError):
        enforce_policy(result.algo_info, allowed_ciphers={"AES256"}, allowed_digests={"SHA1"})


def test_decrypt_wrong_passphrase_fails(gpg_service, us_key, partner_key, fresh_agent_cache):
    encrypted = gpg_service.encrypt_and_sign(
        b"payload",
        recipient_fingerprint=us_key,
        signer_fingerprint=partner_key,
        passphrase="partner-passphrase",
    )
    result = gpg_service.decrypt_and_verify(encrypted, passphrase="wrong-passphrase")
    assert not result.ok


def test_detached_sign_and_verify_roundtrip(gpg_service, us_key):
    data = b"time-c=19960619082855*\r\nrequest-status=ok*\r\nserver-id=coolhost*\r\ntrans-id=1*\r\n"
    signature = gpg_service.detached_sign(data, signer_fingerprint=us_key, passphrase="us-passphrase")

    assert b"BEGIN PGP SIGNATURE" in signature

    result = gpg_service.verify_detached(data, signature, expected_fingerprint=us_key)
    assert result.valid
    assert result.plaintext == data


def test_verify_detached_rejects_tampered_data(gpg_service, us_key):
    data = b"request-status=ok*"
    signature = gpg_service.detached_sign(data, signer_fingerprint=us_key, passphrase="us-passphrase")

    result = gpg_service.verify_detached(b"request-status=EEDM999*", signature, expected_fingerprint=us_key)
    assert not result.valid


def test_verify_detached_rejects_wrong_expected_fingerprint(gpg_service, us_key, partner_key):
    data = b"x"
    signature = gpg_service.detached_sign(data, signer_fingerprint=us_key, passphrase="us-passphrase")
    result = gpg_service.verify_detached(data, signature, expected_fingerprint=partner_key)
    assert not result.valid


def test_verify_receipt_signature_uses_detached_path_for_normal_signatures(gpg_service, us_key):
    data = b"request-status=ok*"
    signature = gpg_service.detached_sign(data, signer_fingerprint=us_key, passphrase="us-passphrase")

    result = gpg_service.verify_receipt_signature(data, signature, expected_fingerprint=us_key)
    assert result.valid


def test_verify_receipt_signature_falls_back_to_inline_signed_message(gpg_service, us_key):
    # Southern Star's TIBCO BusinessConnect receiver (Bouncy Castle FIPS PGP
    # stack) sends a self-contained inline-signed OpenPGP message (one-pass
    # sig + literal data + signature) in the application/pgp-signature part
    # instead of a bare RFC 1847 detached signature -- confirmed live
    # 2026-08-18 via `gpg --list-packets` on the captured bytes, and gpg
    # itself reports "Packet type 4 not allowed in detached signature" when
    # handed this via verify_detached(). Build the same shape with real
    # GnuPG (detach=False) rather than a synthesized guess.
    data = b"time-c=19960619082855*\r\nrequest-status=ok*\r\nserver-id=coolhost*\r\ntrans-id=1*\r\n"
    inline_message = gpg_service.gpg.sign(
        data, keyid=us_key, passphrase="us-passphrase", detach=False, clearsign=False, binary=False
    )
    signature = bytes(inline_message.data)
    assert signature.startswith(b"-----BEGIN PGP MESSAGE-----")

    detached_result = gpg_service.verify_detached(data, signature, expected_fingerprint=us_key)
    assert not detached_result.valid

    result = gpg_service.verify_receipt_signature(data, signature, expected_fingerprint=us_key)
    assert result.valid
    assert result.plaintext == data


def test_verify_receipt_signature_rejects_inline_message_with_mismatched_embedded_data(gpg_service, us_key):
    # A validly-signed inline message whose embedded literal data doesn't
    # match the report_body already parsed from the surrounding MIME
    # structure must still be rejected -- otherwise the fallback would
    # accept a signature over content different from what's actually being
    # trusted as the receipt.
    signed_data = b"request-status=ok*"
    inline_message = gpg_service.gpg.sign(
        signed_data, keyid=us_key, passphrase="us-passphrase", detach=False, clearsign=False, binary=False
    )
    signature = bytes(inline_message.data)

    result = gpg_service.verify_receipt_signature(
        b"request-status=EEDM999*", signature, expected_fingerprint=us_key
    )
    assert not result.valid


def test_check_key_length_accepts_rsa_above_minimum():
    check_key_length(pubkey_algo="1", length_bits=2048, min_bits=2048)


def test_check_key_length_rejects_short_rsa_key():
    with pytest.raises(WeakAlgorithmError):
        check_key_length(pubkey_algo="1", length_bits=1024, min_bits=2048)


def test_check_key_length_rejects_non_rsa_algo():
    with pytest.raises(WeakAlgorithmError):
        check_key_length(pubkey_algo="17", length_bits=4096, min_bits=2048)  # 17 = DSA


def test_encrypt_and_sign_raises_gpg_error_on_unknown_recipient(gpg_service, partner_key):
    with pytest.raises(GpgError):
        gpg_service.encrypt_and_sign(
            b"payload",
            recipient_fingerprint="0000000000000000000000000000000000000",
            signer_fingerprint=partner_key,
            passphrase="partner-passphrase",
        )
