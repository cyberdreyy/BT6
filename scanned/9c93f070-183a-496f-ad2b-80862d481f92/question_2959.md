# Q2959: cache poisoning of another user's result in shard_endpoints.allMembers

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `allMembers` at shard selection for a user trigger request routed to a sharded DON so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `allMembers`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: workflow identifiers that select a shard (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `workflow identifiers that select a shard`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
