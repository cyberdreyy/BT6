# Q0841: concurrent create defeats uniqueness in bridge_type.NewBridgeType

## Question
Can a holder of an external-initiator access-key/secret pair race creations through `NewBridgeType` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs to obtain two records with the same effective name, so job resolution becomes attacker-controlled?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `NewBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `bridge name string (case, unicode, length)`.
- Invariant to test: uniqueness must be enforced by a DB constraint inside the transaction
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrent test asserting a single row survives
