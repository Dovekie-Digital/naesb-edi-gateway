---
name: run-and-review-tests
description: This skill should be used when the user asks to "run the tests", "execute the tests", "run pytest", "check the tests", "did I break anything", "review these tests", or after code changes are made and their correctness/test coverage needs verifying — covers both running the suite correctly (fast vs. integration, Docker dependency) and reviewing test quality against this repo's testing conventions.
---

# Running and reviewing tests

Run this project's test suite the right way (it has two tiers with
different infrastructure requirements) and, when reviewing new or changed
tests, check them against the conventions the existing suite already
establishes — this repo tests real cryptographic/MIME behavior rather than
mocking it, and that invariant is easy to accidentally violate.

## Running the suite

```
.venv/bin/python -m pytest -m "not integration"   # fast suite -- no Docker required
.venv/bin/python -m pytest -m integration          # Postgres-backed, requires Docker (testcontainers)
.venv/bin/ruff check .                              # lint
```

Or run `.claude/skills/run-and-review-tests/scripts/run_tests.sh`, which
does all three in order and skips the integration tier with a clear
message (rather than hanging) if the Docker daemon isn't reachable.

- **Always run the fast suite.** It needs no external services and covers
  the crypto/MIME/envelope/receipt pipeline (against a real `gpg`
  binary, ephemeral RSA-2048 keypairs from `tests/conftest.py`), the
  inbound HTTP flow, the outbound client (`respx`-mocked), sinks
  (filesystem via real temp dirs, S3 via in-process `moto`, webhook),
  and worker retry logic (fake in-memory repositories).
- **Run the integration suite** when the change touches
  `app/tracking/` (repository/DB code), a new/changed migration in
  `db/migrations/`, or anything the worker/tracker queries — that's the
  only place real Postgres behavior (via `testcontainers`) is exercised.
  It needs a running Docker daemon; check with `docker info` first, since
  `testcontainers` fails slowly and unhelpfully if Docker isn't up.
- **Run `ruff check .`** — it's a listed dev dependency and the project's
  only configured lint gate (line-length 100, target py312).

## Interpreting failures

A few failure modes are specific to this repo's GPG-heavy fast suite, not
generic pytest issues — check these before assuming a real regression:

- **`gpg-agent` errors mentioning a socket path or "File name too long"**:
  `tests/conftest.py::gnupg_home` deliberately uses a short `/tmp/naesb-gnupg-*`
  directory instead of pytest's `tmp_path`, because `tmp_path`'s nested
  path overflows the ~100-byte `AF_UNIX` socket path limit and breaks
  `gpg-agent`. If a new fixture/test reintroduces `tmp_path` for a GPG
  homedir, that's the bug, not the test.
- **A wrong-passphrase test passes when it shouldn't**: keypairs are
  module-scoped (`tests/conftest.py::keypair`, expensive RSA keygen not
  worth repeating per-test). If an earlier test in the same module
  already unlocked the key, `gpg-agent` may have cached it. Use the
  `fresh_agent_cache` fixture to clear the cache before a test that
  specifically exercises passphrase-failure behavior.
- **Integration suite hangs or fails with a container/connection error**:
  Docker daemon likely isn't running — confirm with `docker info` before
  re-running.
- **`moto`/`respx` version-shaped failures**: check `pyproject.toml`'s
  pinned minimum versions (`moto[s3]>=5.0`, `respx>=0.21`) match what's
  installed in `.venv`.

## Reviewing test quality

This project's stated testing philosophy (README.md "Testing") is that
**nothing is mocked at the crypto or MIME layer** — tests exercise a real
`gpg` binary and hand-rolled MIME parsing, not stubs. When reviewing a new
or modified test, check it against the pattern the existing suite already
uses for that layer, and flag a deviation as a real finding, not a style
nit:

| Layer | Expected pattern | Existing example |
|---|---|---|
| PGP encrypt/sign/decrypt/verify | Real `gpg` via `GpgService`, ephemeral keypairs from `conftest.py` — never a mocked/stubbed crypto call | `test_gpg_policy.py`, `test_receipt.py` |
| MIME envelope/receipt build+parse | Real byte-level round trip (build → parse), asserting on actual bytes, not a mocked parser | `test_multipart_codec.py`, `test_pgp_mime.py` |
| Outbound HTTP to a partner | `respx`-mocked at the HTTP-transport level, not a hand-rolled fake client | `test_outbound_client.py` |
| S3 sink | In-process `moto` (`mock_aws()`), real `boto3` calls against a fake backend — not a mocked `boto3` client | `test_sinks_s3.py` |
| Filesystem sink | Real temp directory (`tmp_path`), real file I/O | `test_sinks_filesystem.py` |
| Postgres-dependent behavior (tracking, migrations) | `testcontainers[postgres]`, marked `@pytest.mark.integration` — never mocked or skipped silently in the fast suite | `test_tracking_repository.py` |
| Pure retry/dispatch logic (no I/O) | Lightweight fake in-memory repository classes (e.g. `FakeJobRepository`, `FakeTracker`) rather than a real or mocked DB | `test_worker.py` |
| Real-world wire format edge cases | Parse the actual captured transmissions in `samples/request-ssc-*.txt`, not only synthetic `build_multipart_body()` output | `test_sample_request.py` |

Other things worth flagging in review:

- A new `EEDM###`/`WEDM###`/`GWX-...` code (see [[naesb-spec-check]]) added
  without a test asserting the exact code string in the relevant failure
  path (`test_inbound_route.py` is the usual home for these).
- A new sink (see [[new-sink]]) without a test covering both a success
  and a failure path returning `SinkResult(ok=False, ...)`.
- A new migration (see [[db-migration]]) with schema-dependent behavior
  that isn't exercised in `test_tracking_repository.py`.
- Assertions on internal call structure (e.g. asserting a mock was called
  with certain args) where asserting on real output (bytes written,
  HTTP body shape, DB row contents) would catch more — this suite
  consistently prefers the latter.

## Additional resources

- `scripts/run_tests.sh` — runs lint + fast suite always, integration
  suite only if Docker is reachable.
