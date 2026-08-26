# Q5803: revoked workflow still executable in aggregator.Aggregate

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `Aggregate` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `Aggregate`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: repeat requests with mutated identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `repeat requests with mutated identifiers` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
