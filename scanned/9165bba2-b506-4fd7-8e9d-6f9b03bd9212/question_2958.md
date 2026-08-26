# Q2958: cache poisoning of another user's result in response_cache.isCacheableStatusCode

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `isCacheableStatusCode` at the gateway response cache serving repeated user trigger requests so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isCacheableStatusCode`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: the cache key fields of the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `cache key fields of the request`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
