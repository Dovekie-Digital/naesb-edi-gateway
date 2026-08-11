---
name: naesb-spec-check
description: This skill should be used when the user asks to "add a NAESB field", "add an error code", "add a new envelope field", "what does the spec say about X", "is this NAESB-compliant", "check this against the spec", "add support for X per NAESB/Internet ET", or when a change touches app/envelope/*, app/crypto/policy.py, receipt format/fields, auth requirements, retry/Exchange-Failure timing, or any behavior that will be described as spec-mandated rather than a gateway-only extension.
---

# NAESB spec fidelity check

Verify any spec-derived claim against the real manual before writing code
or documentation that asserts it, instead of inferring plausible-sounding
protocol behavior.

## Why this exists

This gateway exchanges real transmissions with real trading partners
(interstate pipeline operators), so any wire format, receipt format, or
error code that isn't actually what the real standard specifies risks
producing a gateway that doesn't interoperate. The only authoritative
source is the real **NAESB WGQ Cybersecurity Related Standards Manual,
Version 4.0** (`docs/NAESB-cyber0923-2026-0709.pdf`) — every spec claim in
this repo must be traceable to it, not inferred from what seems plausible.

## Procedure

1. **Check what's already established first.** `README.md` ("Spec
   provenance -- read before connecting a real trading partner") and
   `docs/PLAN.md`'s design-decision table already resolve most
   spec-vs-extension questions, with standard numbers cited (e.g.
   "standards 12.3.10/12.3.11" for retry timing, "Appendix A" for RSA key
   length, "12.3.26" for the cipher/digest non-mandate). If the question
   is already answered there, use that answer and cite the same section
   number — don't re-derive it from scratch.

2. **If not already established, search the real manual.** The manual is
   `docs/NAESB-cyber0923-2026-0709.pdf` (~70 pages). Two ways to search it:
   - Run `.claude/skills/naesb-spec-check/scripts/search_manual.sh
     "<term>"` to full-text search the PDF (requires `pdftotext` from
     `poppler-utils`; the script prints the install command if missing).
     This is the fast path — grep once instead of paging through the PDF.
   - Or read specific page ranges with the Read tool's PDF `pages`
     parameter (max 20 pages/request) once `poppler` is installed
     (`brew install poppler`) — needed either way, since Read's PDF
     support also shells out to poppler.

3. **Cite what was found.** Reference the standard/section number in the
   code comment or commit message, matching the existing style (grep
   `app/` for examples: `standard 12.3.5`, `Appendix A`, `Table 1`). A
   claim with no citation is a guess, not a spec fact.

4. **If the manual is silent, ambiguous, or doesn't cover the case, do
   not invent a default.** `docs/PLAN.md` documents two cases resolved
   this way rather than guessed:
   - `version` (the wire protocol version, e.g. `1.9`) has no safe
     default — it's required config (`envelope.default_version`),
     confirmed per Trading Partner Agreement.
   - `transaction-set` is treated as an opaque, length-8-validated string
     because the real WGQ 8-character code table wasn't available.

   Follow the same pattern for any new open question: make it required
   config with no default, or ask the project owner directly. Never ship
   a plausible-sounding guess to a real trading partner.

5. **Keep extensions clearly labeled.** Anything with no basis in the
   spec — `api_key`/Bearer auth (`partners.yaml`'s `type: api_key`),
   `GWX-...` error codes (`app/envelope/error_codes.py::GatewayExtensionCode`),
   content-digest dedup — must stay namespaced/labeled as a gateway-only
   extension, never presented as NAESB-mandated. New error/warning codes
   for gateway-detected conditions the spec doesn't cover go in
   `GatewayExtensionCode` with the `GWX-` prefix, specifically so they can
   never collide with a real `EEDM###`/`WEDM###` code.

6. **Never call this project's transport "AS2".** It's NAESB's own
   Internet ET protocol — a different standard, even though the wire
   shape (PGP over HTTP multipart) looks superficially similar.

## Additional resources

- `scripts/search_manual.sh` — full-text search helper over the PDF manual.
