# Q1569: per-owner quota not enforced in aggregator.methodSupportsSignedOCRValidation

## Question
Does `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user enforce rate/quota per authenticated owner, or can any internet client with an arbitrary externally-owned key sending signed gateway requests rotate identifiers to obtain unlimited DON execution charged elsewhere?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `method selection that toggles signed validation` across submissions.
- Invariant to test: quotas must key on the verified owner and be enforced before dispatch
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating identifiers and asserting the quota still applies
