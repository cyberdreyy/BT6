# Q5502: transaction boundary leaves half-applied state in cache.CreateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) abort mid-write at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read so `CreateBridgeType` leaves a record whose credential is unset but which still authenticates or resolves?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `CreateBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Interrupt `bridge name used as cache key` between the writes.
- Invariant to test: record creation must be atomic with its credential
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no record exists without its credential
