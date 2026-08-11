---
name: onboard-partner
description: This skill should be used when the user asks to "onboard a new trading partner", "add a partner", "connect to a new pipeline", "set up a new partner", or wants to configure this gateway to exchange transmissions with a new NAESB trading partner (interstate pipeline operator).
---

# Onboarding a new trading partner

Walk through connecting this gateway to a new NAESB trading partner: this
is mostly a config + operational-checklist task (`config/partners.yaml`,
PGP key exchange, TLS/port/IP prerequisites), not a code change. Don't
guess at any value below marked "confirm with partner" — see
[[naesb-spec-check]] for why guessing at spec/TPA-negotiated values is
explicitly the wrong move in this project.

## 1. Gather values from the Trading Partner Worksheet (TPW)

Before touching config, a bilateral TPW/TPA exchange with the partner
should have produced (README.md "Operational prerequisites"):

- Partner's DUNS number
- Partner's Internet ET endpoint URL
- Partner's PGP public key (get the `.asc` file from them directly, not a
  file they merely reference)
- The Internet ET **protocol** `version` this partner uses (e.g. `1.9` —
  not the manual's "4.0" revision number; no safe default exists, see
  README.md's "`version`/`transaction-set`" note)
- Agreed `transaction-set` code(s), if known
- Whether this partner uses `refnum`/`refnum-orig` tracking
- Inbound auth: the credential *this gateway issues to the partner*
  (prefer HTTP Basic — the spec-compliant default per standards
  12.3.14/12.3.28/12.3.29; `api_key`/Bearer is a gateway-only extension,
  only use it if the partner specifically requires it)
- Outbound auth: the credential *the partner issues to this gateway*
- Any cipher/digest outside this gateway's default accept-list
  (`AES256/AES192/AES128` ciphers, `SHA256/SHA384/SHA512/SHA1` digests) —
  common with older partner PGP implementations (see `samples/request-ssc-*.txt`,
  a real capture requesting `sha1`)
- Whether the partner's on-file key is below the 2048-bit RSA floor (a
  real NAESB minimum, Appendix A) — this should be rare and needs an
  explicit, documented exception if so

## 2. Import the partner's PGP public key

Import into the same GnuPG keyring this gateway's own key lives in
(`crypto.gnupg_home` in `config.yaml`):
```
gpg --homedir <gnupg_home> --import partner-public-key.asc
```
Reference the key file's path from `partners.yaml`'s `pgp_public_key_path`
— startup (`app/crypto/keyring.py`) imports it automatically and rejects
startup if it reports RSA key length below `crypto.min_rsa_key_bits`.

## 3. Add the partner entry to `config/partners.yaml`

Follow `config/partners.example.yaml`'s structure and comments exactly —
it documents every field's meaning inline. Minimum shape:

```yaml
partners:
  - name: <short-label>              # config-file label, NOT the wire identifier
    duns: "<partner-duns>"           # the actual wire identifier (envelope from/to)
    endpoint_url: "https://..."
    pgp_public_key_path: /path/to/partner.pub.asc
    outbound_auth:
      type: basic
      username: <issued-by-partner>
      password_env: NAESB_<PARTNER>_PASSWORD
    inbound_auth:
      type: basic
      username: <issued-to-partner>
      password_env: NAESB_<PARTNER>_INBOUND_PASSWORD
    envelope_overrides:
      version: "<confirmed-with-partner>"
      agreed_transaction_sets: null   # or ["NOM00001", ...] once confirmed
      use_refnum: false               # true if partner uses refnum tracking
```

Add `crypto_overrides` only if step 1 surfaced a real deviation (non-default
cipher/digest, or a documented legacy sub-2048-bit key) — leave it unset
otherwise, per the comments in `config/partners.example.yaml`.

Add every `*_env` secret referenced above to `config/.env` (gitignored,
loaded by docker-compose or sourced manually — never inline a secret
directly into `partners.yaml`).

## 4. Send this gateway's public key to the partner

```
gpg --homedir <gnupg_home> --armor --export <this-gateway-key-id> > public_key.asc
```
Send `public_key.asc` to the partner through whatever channel the TPW
process specifies.

## 5. Confirm infrastructure prerequisites (README.md "Operational prerequisites")

- The reverse proxy in front of this gateway is reachable on one of
  NAESB Appendix C's allowed TCP ports (443, 5713, 6112, 6304, 6874, 7403,
  or a mutually agreed alternate).
- If the partner requires a whitelisted static outbound IP, set
  `server.outbound_source_address` in `config.yaml`.
- NTP is running (standard 12.3.7 requires ±5s clock sync).

## 6. Verify end to end before going live

Follow `docs/PLAN.md`'s "Verification (end-to-end, manual)" section,
adapted to the real partner:

1. `POST {server.inbound_path}` a real (or partner-provided test)
   transmission; confirm HTTP 200, `multipart/signed` response,
   `request-status=ok*`, a sequential `trans-id`.
2. Confirm the decrypted payload lands in the configured sink(s).
3. `POST /outbound/send` to the partner; confirm `202`, then poll
   `GET /outbound/jobs/{id}` through to `delivered`.
4. Negative paths worth confirming once: resend the identical payload
   (dedup fires — `GWX-DUPLICATE-DIGEST` or `EEDM121` depending on
   `use_refnum`), and confirm auth failures return a plain unsigned 401
   rather than a signed receipt.

Don't mark onboarding complete until at least one real (or
partner-sandbox) round trip has succeeded — config that merely
schema-validates isn't proof the DUNS, key, auth, and endpoint all agree
with what the partner actually has on their side.
