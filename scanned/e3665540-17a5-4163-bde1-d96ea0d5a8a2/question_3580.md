# Q3580: upsert overwrites another owner's record in cache.DeleteBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) drive `DeleteBridgeType` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read to upsert over a record they do not own (bridge, initiator, cached response) because the write is keyed only by name?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `DeleteBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: update timing versus the cache refresh interval (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update timing versus the cache refresh interval` matching the victim record's key.
- Invariant to test: writes must be scoped by ownership, not only by name
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test upserting a foreign record
