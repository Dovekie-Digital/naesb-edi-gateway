#!/usr/bin/env python3
"""Replay a captured request-line+headers+multipart-body transcript (see
samples/request-ssc-*.txt) against a live naesb-edi-gateway /inbound
endpoint. Reconstructs the body/content-type the same way
tests/test_sample_request.py::_load_sample does (bare-LF -> CRLF
normalization, split head/body, extract the real Content-Type header
including its boundary parameter), then POSTs it with HTTP Basic auth so a
real deployment can be validated against real captured partner traffic
without needing the partner's private key or any synthetic payload.

Usage:
  NAESB_TEST_PASSWORD=... python scripts/send_sample.py samples/request-ssc-1.txt \\
      --url https://test.edi.fidelis-energy.com/inbound \\
      --username southern-star-inbound
"""

import argparse
import os
import sys
from pathlib import Path

import httpx


# Framing headers a real HTTP client must compute/set itself for the
# request it's actually sending (a different host, a body that may differ
# in byte-for-byte length after CRLF normalization) -- forwarding the
# captured values for these specifically would be wrong, not faithful.
_SKIP_HEADERS = {"host", "content-length"}


def load_sample(path: Path) -> tuple[bytes, dict[str, str]]:
    """Returns (body, headers) reconstructed from a captured request-line+
    headers+multipart-body transcript, preserving every other real header
    from the capture (Date, mime-version, message-id, User-Agent, etc.) --
    these are part of what's being simulated, not just Content-Type."""
    raw = path.read_bytes().replace(b"\n", b"\r\n")
    head, _, body = raw.partition(b"\r\n\r\n")
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n")[1:]:
        if not line.strip():
            continue
        name, _, value = line.partition(b":")
        name_str = name.strip().decode()
        if name_str.lower() in _SKIP_HEADERS:
            continue
        headers[name_str] = value.strip().decode()
    if "content-type" not in {k.lower() for k in headers}:
        raise ValueError(f"{path}: no content-type header found in capture")
    return body, headers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sample", type=Path, help="path to a captured request transcript")
    parser.add_argument("--url", required=True, help="target /inbound URL")
    parser.add_argument("--username", default=os.environ.get("NAESB_TEST_USERNAME"))
    parser.add_argument(
        "--password",
        default=os.environ.get("NAESB_TEST_PASSWORD"),
        help="defaults to $NAESB_TEST_PASSWORD -- avoid passing this directly as an arg",
    )
    args = parser.parse_args()

    if not args.username or not args.password:
        parser.error("--username/--password required (or set NAESB_TEST_USERNAME/NAESB_TEST_PASSWORD)")

    body, headers = load_sample(args.sample)

    response = httpx.post(
        args.url,
        content=body,
        headers=headers,
        auth=(args.username, args.password),
        timeout=30.0,
    )

    print(f"HTTP {response.status_code}")
    print(response.text)
    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
