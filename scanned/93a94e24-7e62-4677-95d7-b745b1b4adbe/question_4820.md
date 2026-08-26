# Q4820: hex/address normalization differences in gateway.NewGateway

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present an address or identifier at ProcessRequest on the public gateway user endpoint in a casing/encoding variant that `NewGateway` treats as distinct from the allowlisted form, bypassing an authorization or quota keyed on the other form?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `NewGateway`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: method and donId routing fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method and donId routing fields` in mixed case, checksummed, zero-padded or 0x-less form.
- Invariant to test: identifiers must be canonicalized once before any authorization or accounting decision
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over normalization for all encodings of one identity
