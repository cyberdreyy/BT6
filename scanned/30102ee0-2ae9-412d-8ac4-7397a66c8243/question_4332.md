# Q4332: cache serves a stale or foreign bridge in bridge_type.ParseBridgeName

## Question
Can a holder of an external-initiator access-key/secret pair exploit the cache in `ParseBridgeName` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs so a deleted, disabled or replaced bridge keeps being used, or a name collision returns another owner's bridge?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `ParseBridgeName`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Race `bridge name string (case, unicode, length)` against the refresh interval.
- Invariant to test: the cache must be invalidated transactionally with the write and keyed unambiguously
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrency test mutating a bridge while reads run
