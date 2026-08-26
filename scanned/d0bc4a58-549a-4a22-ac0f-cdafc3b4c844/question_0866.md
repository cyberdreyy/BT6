# Q0866: duplicate response overwrites the result in aggregator.methodSupportsSignedOCRValidation

## Question
Can a second response accepted by `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user overwrite an already-delivered result so any internet client with an arbitrary externally-owned key sending signed gateway requests changes what a workflow consumes?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force duplicates via `method selection that toggles signed validation`.
- Invariant to test: response processing must be single-shot per request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test sending duplicate responses and asserting the first wins
