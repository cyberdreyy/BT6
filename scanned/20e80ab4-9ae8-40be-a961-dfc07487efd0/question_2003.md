# Q2003: transaction boundary leaves half-applied state in bridge_type.AuthenticateBridgeType

## Question
Can a holder of an external-initiator access-key/secret pair abort mid-write at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs so `AuthenticateBridgeType` leaves a record whose credential is unset but which still authenticates or resolves?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `AuthenticateBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Interrupt `bridge name string (case, unicode, length)` between the writes.
- Invariant to test: record creation must be atomic with its credential
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no record exists without its credential
