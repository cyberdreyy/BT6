# Q5275: length/alignment helper mismatch in httpserver.splitURL

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the public gateway user HTTP endpoint (POST to the configured user path) whose alignment or length handling in `splitURL` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `splitURL`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request body bytes and Content-Length (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request body bytes and Content-Length` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
