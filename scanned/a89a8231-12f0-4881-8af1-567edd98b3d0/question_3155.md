# Q3155: duplicate response overwrites the result in aggregator.signedResponseRequestIDEnabled

## Question
Can a second response accepted by `signedResponseRequestIDEnabled` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user overwrite an already-delivered result so any internet client with an arbitrary externally-owned key sending signed gateway requests changes what a workflow consumes?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `signedResponseRequestIDEnabled`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: the request fields that derive the signed request id (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force duplicates via `request fields that derive the signed request id`.
- Invariant to test: response processing must be single-shot per request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test sending duplicate responses and asserting the first wins
