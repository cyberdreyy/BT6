# Q1023: signature validation optional by method in aggregator.methodSupportsSignedOCRValidation

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at aggregation and signature/quorum validation of vault node responses before they reach the requesting user for which `methodSupportsSignedOCRValidation` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: the request fields that derive the signed request id (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `request fields that derive the signed request id` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
