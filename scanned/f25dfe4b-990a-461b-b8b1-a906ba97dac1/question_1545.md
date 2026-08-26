# Q1545: cache serves a stale or foreign bridge in orm.transact

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) exploit the cache in `transact` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs so a deleted, disabled or replaced bridge keeps being used, or a name collision returns another owner's bridge?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `transact`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: concurrent create/update requests (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Race `concurrent create/update requests` against the refresh interval.
- Invariant to test: the cache must be invalidated transactionally with the write and keyed unambiguously
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrency test mutating a bridge while reads run
