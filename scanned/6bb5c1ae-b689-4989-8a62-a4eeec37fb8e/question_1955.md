# Q1955: first-to-quorum accepts attacker-shaped result in requestcache.NewRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request at the gateway request cache keyed per user request so the first aggregator to reach quorum in `NewRequest` returns a result derived from attacker-controlled input rather than the intended workflow output?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `NewRequest`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: the request id/key fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request id/key fields` designed to satisfy the weaker aggregator first.
- Invariant to test: all aggregators must apply identical verification before producing a user response
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing verification across aggregators
