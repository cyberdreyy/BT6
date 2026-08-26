# Q5951: cache serves a stale or foreign bridge in orm.CreateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) exploit the cache in `CreateBridgeType` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs so a deleted, disabled or replaced bridge keeps being used, or a name collision returns another owner's bridge?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `CreateBridgeType`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: external initiator name (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Race `external initiator name` against the refresh interval.
- Invariant to test: the cache must be invalidated transactionally with the write and keyed unambiguously
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrency test mutating a bridge while reads run
