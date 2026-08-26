# Q0629: cache poisoning of another user's result in aggregator.methodSupportsSignedOCRValidation

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `method selection that toggles signed validation`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
