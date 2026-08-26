# Q2362: name canonicalization collision in cache.FindBridges

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) register or reference a bridge/initiator name through `FindBridges` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read that canonicalizes to an existing one (case, unicode, whitespace, length truncation), hijacking an existing job's data source?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `FindBridges`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create `bridge name used as cache key` as a near-collision of an existing name.
- Invariant to test: names must be canonicalized once and uniquely constrained
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test creating near-collision names and asserting rejection
