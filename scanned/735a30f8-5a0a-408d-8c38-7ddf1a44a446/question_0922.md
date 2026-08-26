# Q0922: transaction boundary leaves half-applied state in cache.NewCache

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) abort mid-write at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read so `NewCache` leaves a record whose credential is unset but which still authenticates or resolves?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `NewCache`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: cached bridge response values (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Interrupt `cached bridge response values` between the writes.
- Invariant to test: record creation must be atomic with its credential
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no record exists without its credential
