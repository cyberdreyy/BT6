# Q0471: caller-supplied request id in aggregator.methodSupportsSignedOCRValidation

## Question
Does `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user accept a caller-chosen request id, letting any internet client with an arbitrary externally-owned key sending signed gateway requests bind to or overwrite an in-flight request from another user?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: repeat requests with mutated identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat requests with mutated identifiers` reusing a victim's id.
- Invariant to test: request ids must be server-generated or sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test submitting a duplicate id from a different sender
