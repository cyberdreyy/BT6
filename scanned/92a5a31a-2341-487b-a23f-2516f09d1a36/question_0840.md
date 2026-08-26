# Q0840: concurrent create defeats uniqueness in external_initiator.NewExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair race creations through `NewExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs to obtain two records with the same effective name, so job resolution becomes attacker-controlled?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the job id targeted (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `job id targeted`.
- Invariant to test: uniqueness must be enforced by a DB constraint inside the transaction
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrent test asserting a single row survives
