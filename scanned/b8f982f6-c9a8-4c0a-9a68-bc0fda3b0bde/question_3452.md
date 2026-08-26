# Q3452: cache serves a stale or foreign bridge in bridge_type.MarshalBridgeMetaData

## Question
Can a holder of an external-initiator access-key/secret pair exploit the cache in `MarshalBridgeMetaData` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs so a deleted, disabled or replaced bridge keeps being used, or a name collision returns another owner's bridge?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `MarshalBridgeMetaData`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the bridge JSON body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Race `bridge JSON body` against the refresh interval.
- Invariant to test: the cache must be invalidated transactionally with the write and keyed unambiguously
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrency test mutating a bridge while reads run
