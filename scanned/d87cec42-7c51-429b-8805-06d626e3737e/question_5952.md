# Q5952: cache serves a stale or foreign bridge in cache.UpdateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) exploit the cache in `UpdateBridgeType` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read so a deleted, disabled or replaced bridge keeps being used, or a name collision returns another owner's bridge?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `UpdateBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: cached bridge response values (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Race `cached bridge response values` against the refresh interval.
- Invariant to test: the cache must be invalidated transactionally with the write and keyed unambiguously
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrency test mutating a bridge while reads run
