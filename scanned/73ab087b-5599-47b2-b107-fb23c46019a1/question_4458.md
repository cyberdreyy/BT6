# Q4458: upsert overwrites another owner's record in cache.BridgeTypes

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) drive `BridgeTypes` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read to upsert over a record they do not own (bridge, initiator, cached response) because the write is keyed only by name?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `BridgeTypes`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name used as cache key` matching the victim record's key.
- Invariant to test: writes must be scoped by ownership, not only by name
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test upserting a foreign record
