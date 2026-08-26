# Q0604: upsert overwrites another owner's record in bridge_type.NewBridgeType

## Question
Can a holder of an external-initiator access-key/secret pair drive `NewBridgeType` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs to upsert over a record they do not own (bridge, initiator, cached response) because the write is keyed only by name?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `NewBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name string (case, unicode, length)` matching the victim record's key.
- Invariant to test: writes must be scoped by ownership, not only by name
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test upserting a foreign record
