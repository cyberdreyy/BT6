# Q0155: authorization key check bypassed in aggregator.methodSupportsSignedOCRValidation

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests pass the authorization/allowlist check reached by `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user with a missing, empty or differently-encoded key, obtaining unauthorized DON execution?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `method selection that toggles signed validation` with the key absent/empty/re-encoded.
- Invariant to test: authorization must fail closed and compare canonicalized values
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over the authorization check with degenerate keys
