# Q1310: name canonicalization collision in bridge_type.AuthenticateBridgeType

## Question
Can a holder of an external-initiator access-key/secret pair register or reference a bridge/initiator name through `AuthenticateBridgeType` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs that canonicalizes to an existing one (case, unicode, whitespace, length truncation), hijacking an existing job's data source?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `AuthenticateBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create `bridge name string (case, unicode, length)` as a near-collision of an existing name.
- Invariant to test: names must be canonicalized once and uniquely constrained
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test creating near-collision names and asserting rejection
