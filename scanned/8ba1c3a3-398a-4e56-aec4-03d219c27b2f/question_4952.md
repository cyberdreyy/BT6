# Q4952: cache poisoning of another user's result in requestcache.deleteAndSendOnce

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `deleteAndSendOnce` at the gateway request cache keyed per user request so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `deleteAndSendOnce`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: response arrival ordering (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `response arrival ordering`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
