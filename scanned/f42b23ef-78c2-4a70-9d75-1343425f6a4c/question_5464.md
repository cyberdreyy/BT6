# Q5464: error response discloses secret material in aggregator.Aggregate

## Question
Do error paths in `Aggregate` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `Aggregate`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: repeat requests with mutated identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `repeat requests with mutated identifiers`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
