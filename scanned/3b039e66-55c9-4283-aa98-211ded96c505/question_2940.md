# Q2940: transaction boundary leaves half-applied state in bridge_type.incomingTokenHash

## Question
Can a holder of an external-initiator access-key/secret pair abort mid-write at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs so `incomingTokenHash` leaves a record whose credential is unset but which still authenticates or resolves?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `incomingTokenHash`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the incoming access key/token (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Interrupt `incoming access key/token` between the writes.
- Invariant to test: record creation must be atomic with its credential
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no record exists without its credential
