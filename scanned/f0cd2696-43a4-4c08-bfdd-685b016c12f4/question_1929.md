# Q1929: concurrent create defeats uniqueness in external_initiator.AuthenticateExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair race creations through `AuthenticateExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs to obtain two records with the same effective name, so job resolution becomes attacker-controlled?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `AuthenticateExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the accessKey/secret headers (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `accessKey/secret headers`.
- Invariant to test: uniqueness must be enforced by a DB constraint inside the transaction
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrent test asserting a single row survives
