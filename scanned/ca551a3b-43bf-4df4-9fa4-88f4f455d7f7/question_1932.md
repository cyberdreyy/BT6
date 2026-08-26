# Q1932: concurrent create defeats uniqueness in cache.FindBridge

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) race creations through `FindBridge` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read to obtain two records with the same effective name, so job resolution becomes attacker-controlled?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `FindBridge`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `bridge name used as cache key`.
- Invariant to test: uniqueness must be enforced by a DB constraint inside the transaction
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrent test asserting a single row survives
