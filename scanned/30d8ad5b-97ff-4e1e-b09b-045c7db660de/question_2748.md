# Q2748: deserialization accepts hostile fields in bridge_type.incomingTokenHash

## Question
Can a holder of an external-initiator access-key/secret pair submit a payload at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs whose unmarshalling in `incomingTokenHash` sets fields the API does not expose (id, owner, token, created_at), taking over an existing record?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `incomingTokenHash`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the incoming access key/token (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `incoming access key/token` with extra JSON fields.
- Invariant to test: unmarshalling must reject unknown and server-owned fields
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test posting bodies with server-owned fields
