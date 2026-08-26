# Q1703: hex/address normalization differences in httpserver.ensureLimiters

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present an address or identifier at the public gateway user HTTP endpoint (POST to the configured user path) in a casing/encoding variant that `ensureLimiters` treats as distinct from the allowlisted form, bypassing an authorization or quota keyed on the other form?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `ensureLimiters`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request body bytes and Content-Length (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request body bytes and Content-Length` in mixed case, checksummed, zero-padded or 0x-less form.
- Invariant to test: identifiers must be canonicalized once before any authorization or accounting decision
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over normalization for all encodings of one identity
