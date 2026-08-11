---
name: new-sink
description: This skill should be used when the user asks to "add a sink", "add a new delivery destination", "deliver inbound messages to X" (e.g. Kafka, SFTP, Azure Blob, an internal queue), "add a new sink type", or wants inbound transmissions fanned out somewhere beyond the existing filesystem/S3/webhook sinks.
---

# Adding a new inbound delivery sink

Wire a new delivery destination into the existing sink fan-out
architecture (`app/sinks/`) rather than special-casing delivery logic
elsewhere. Every sink implements the same small protocol and plugs into
the same dispatcher, config loader, and app-startup wiring point.

## The Sink protocol

`app/sinks/base.py` defines the contract every sink must satisfy:

```python
class Sink(Protocol):
    name: str
    durable: bool
    async def deliver(self, message: InboundMessage) -> SinkResult: ...
```

- `name` — a short stable string; becomes the key in the message's
  `sinks_status` tracking dict and in fan-out results.
- `durable` — whether a *successful* delivery counts toward the
  "at least one durable sink must succeed" requirement
  (`app/sinks/dispatcher.py::has_durable_success()`) that gates whether
  an inbound transmission gets ACKed at all. Filesystem/S3 default `True`
  (they retain the content); webhook defaults `False` (best-effort
  notification, not storage). Decide which this new sink is — a
  best-effort notification sink (Slack, a webhook-like queue) should be
  `durable: False`; anything that durably retains the payload should be
  `True`.
- `deliver()` must not raise for expected failure modes — catch them and
  return `SinkResult(ok=False, error=...)`. `dispatcher.py::_deliver_safely()`
  already isolates unexpected exceptions from other sinks, but returning
  a proper `SinkResult` gives a real error message in tracking instead of
  a generic exception string.

## Steps

1. **Implement the sink** in `app/sinks/<name>_sink.py`, modeled on
   `app/sinks/filesystem_sink.py` (simplest, synchronous-work-via-
   `asyncio.to_thread`) or `app/sinks/s3_sink.py` (async network client)
   depending on whether the destination's client library is sync or
   async. Constructor takes whatever connection config it needs plus
   `durable: bool = <default>`. Key any written content by
   `message.envelope.from_id` (the DUNS, the canonical wire identifier —
   not `message.partner_name`, which is only the config-file label) the
   same way `filesystem_sink.py` does, unless the destination has its own
   natural addressing scheme.

2. **Add a config model** to `app/settings.py` next to
   `FilesystemSinkConfig`/`S3SinkConfig`/`WebhookSinkConfig`: an
   `enabled: bool = False` flag, a `durable: bool` default matching the
   decision above, whatever connection fields are needed, and secrets as
   `..._env: str` fields resolved via the existing `resolve_env()`
   pattern (see `S3SinkConfig.access_key`/`secret_key` properties) —
   never inline a secret directly in `config.yaml`. Add the new field to
   `SinksConfig`.

3. **Wire construction** into `app/main.py::_build_sinks()`, following
   the existing `if <cfg>.enabled:` + `sinks.append(...)` pattern for
   each existing sink.

4. **Document the config** in `config/config.example.yaml` (commented
   example block, matching the style already used for `sinks.s3`) so a
   fresh operator can find and enable it. Mention the new sink in
   README.md's "Inbound delivery fans out to..." bullet if it changes the
   ACK-eligibility picture (i.e. if it's durable).

5. **Write tests** in `tests/test_sinks_<name>.py`, following
   `tests/test_sinks_filesystem.py`'s shape: a `_message(**overrides)`
   helper building a real `InboundMessage`/`EnvelopeFields`, then cases
   for successful delivery, the DUNS-keyed addressing, and a failure path
   returning `SinkResult(ok=False, ...)` rather than raising. If the
   destination has a network client with a fake/local test double
   available (the way `moto` in-process-fakes S3 for
   `tests/test_sinks_s3.py`), prefer that over mocking `deliver()`
   internals — the existing tests exercise real client behavior, not
   mocked stubs.

6. **Confirm the dispatcher picture still holds**: with the new sink
   enabled, `dispatcher.fan_out()` runs it concurrently with the others,
   and one sink failing (this one or any other) must not prevent the
   others from delivering. No dispatcher code changes are needed for a
   well-behaved new sink — if it seems like the dispatcher needs
   changing, that's a signal the new sink isn't fitting the existing
   protocol cleanly.
