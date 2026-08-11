# naesb-edi-gateway

NAESB WGQ 4.0 Internet ET gateway: PGP-encrypted EDI over HTTP between
internal systems and external pipeline trading partners. No UI, pure
HTTP/API service. Full context lives in `README.md` (spec provenance,
architecture, API reference, operational prerequisites) and
`docs/PLAN.md` (directory tree, design-decision table with rationale,
wire-format examples, testing strategy) — read those before making
non-trivial changes rather than re-deriving decisions that are already
documented and reasoned through there. `docs/inbound-flow.md` /
`docs/outbound-flow.md` / `docs/authentication.md` walk each pipeline
stage against the actual code.

## The one rule that matters most here

**Never guess at NAESB spec behavior.** Every spec-derived claim in this
codebase must trace back to the real **NAESB WGQ Cybersecurity Related
Standards Manual, Version 4.0** (`docs/NAESB-cyber0923-2026-0709.pdf`) —
this is real trading-partner infrastructure, and inventing plausible-sounding
protocol behavior risks shipping something that doesn't interoperate. So:

- Any change that touches envelope fields, the receipt format, error
  codes, crypto requirements, or protocol semantics must be traceable to
  a specific section/standard number in the real PDF manual, or to
  something already established in `README.md`/`docs/PLAN.md`. Cite the
  standard number in commit messages / comments for new spec-derived
  behavior, the way existing code does (e.g. "standards 12.3.10/12.3.11").
- If the manual is silent or ambiguous on something (e.g. the exact
  `version` field default, the `transaction-set` code table), do not
  invent a plausible-sounding default — leave it as required config with
  no default (see `envelope.default_version`) or ask the project owner,
  the way `docs/PLAN.md` documents those two cases were resolved.
- Gateway-only behavior that has no basis in the spec (e.g. `api_key`
  auth, `GWX-...` error codes, content-digest dedup) must stay clearly
  labeled as a local extension, never presented as spec-mandated.
- Never call this project's transport "AS2" — it's NAESB's own Internet ET
  protocol, a different (if superficially similar) standard.

## Architecture (see README.md "Processes" for full detail)

Three independent processes sharing one Postgres DB, GPG keyring, and
partner config:

- `app` (`uvicorn app.main:app`) — synchronous inbound HTTP server; also
  enqueues outbound jobs but never delivers them.
- `worker` (`python -m app.worker`) — the only process that performs an
  outbound delivery attempt; owns retry/Exchange-Failure scheduling.
- `poller` (`python -m app.poller`, opt-in) — alternate on-ramp into the
  same outbound queue, watching a filesystem drop folder.

## Conventions to preserve

- **MIME is hand-rolled**, not built via Python's `email` package
  (`app/envelope/mime_split.py`, `pgp_mime.py`, `multipart_codec.py`,
  `receipt.py`) — PGP signatures are byte-exact and `email.generator`
  doesn't guarantee byte-identical re-serialization. Don't introduce a
  round-trip through `email`/similar for anything that gets signed or
  verified.
- **Error codes**: real `EEDM###`/`WEDM###` codes (`app/envelope/error_codes.py::NaesbErrorCode`)
  are spec-defined and must match Table 1 of the manual exactly. Gateway
  extensions live in `GatewayExtensionCode` with a `GWX-` prefix
  specifically so they can never collide with a real code — keep any new
  non-spec error/warning in that namespace.
- **Config**: `config/config.yaml` (global) + `config/partners.yaml`
  (per-partner, override-merges onto global) — both YAML, both gitignored
  in their real (non-`.example.yaml`) form. Secrets are referenced by
  `*_env` keys resolved from `config/.env`, never inlined.
- **Sinks** implement the `Sink` protocol (`app/sinks/base.py`: `name`,
  `durable: bool`, `async deliver()`); register new ones in
  `app/sinks/dispatcher.py`. Acknowledging a transmission requires at
  least one *durable* sink to succeed — don't let a new non-durable sink
  (like webhook) count toward that.
- **DB migrations**: numbered idempotent SQL files in `db/migrations/`
  (`NNNN_description.sql`), applied in filename order at startup by
  `app/tracking/db.py`. No ORM/Alembic — keep it that way per
  `docs/PLAN.md`.
- **Dedup/idempotency**: prefer `(partner, refnum)` when a partner has
  `use_refnum: true`; otherwise fall back to a SHA-256 digest of the
  extracted ciphertext bytes (not the raw multipart body).

## Commands

```
pytest -m "not integration"    # fast suite, no Docker
pytest -m integration          # Postgres-backed (testcontainers), needs Docker
ruff check .                   # lint (line-length 100, py312)
docker-compose up --build      # app + worker + poller + postgres + minio
```

The fast test suite uses real `gpg` (ephemeral RSA-2048 keypairs, not
mocked) — crypto/MIME correctness is verified against the real binary, not
assumptions about its behavior.

## Where to look

| Need to... | Look at |
|---|---|
| Understand full spec provenance / API surface | `README.md` |
| See *why* a design decision was made | `docs/PLAN.md`'s decision table |
| Trace one inbound transmission step-by-step | `docs/inbound-flow.md` |
| Trace one outbound transmission step-by-step | `docs/outbound-flow.md` |
| Understand the two independent auth mechanisms per partner | `docs/authentication.md` |
| Check a claim against the actual standard | `docs/NAESB-cyber0923-2026-0709.pdf` |
| See real-world (not synthetic) transmission shapes | `samples/request-ssc-*.txt`, `test_sample_request.py` |
