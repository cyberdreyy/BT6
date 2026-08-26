# Q0682: deserialization accepts hostile fields in external_initiator.NewExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair submit a payload at the external-initiator authenticated route POST /v2/jobs/:ID/runs whose unmarshalling in `NewExternalInitiator` sets fields the API does not expose (id, owner, token, created_at), taking over an existing record?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the run request body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `run request body` with extra JSON fields.
- Invariant to test: unmarshalling must reject unknown and server-owned fields
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test posting bodies with server-owned fields
