# Q5916: first-to-quorum accepts attacker-shaped result in requestcache.deleteAndSendOnce

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request at the gateway request cache keyed per user request so the first aggregator to reach quorum in `deleteAndSendOnce` returns a result derived from attacker-controlled input rather than the intended workflow output?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `deleteAndSendOnce`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: repeat and concurrent submissions (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat and concurrent submissions` designed to satisfy the weaker aggregator first.
- Invariant to test: all aggregators must apply identical verification before producing a user response
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing verification across aggregators
