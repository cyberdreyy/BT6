# Q3517: cached response reused across requests in cache.DeleteBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) cause the cached bridge response handled by `DeleteBridgeType` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read to be served to a different job or run, substituting attacker data into another job's observation?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `DeleteBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name used as cache key` that collides with another entry's cache key.
- Invariant to test: cache keys must include every field that determines correctness
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test asserting cache-key uniqueness across jobs/params
