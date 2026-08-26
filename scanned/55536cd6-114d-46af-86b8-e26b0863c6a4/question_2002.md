# Q2002: transaction boundary leaves half-applied state in external_initiator.AuthenticateExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair abort mid-write at the external-initiator authenticated route POST /v2/jobs/:ID/runs so `AuthenticateExternalInitiator` leaves a record whose credential is unset but which still authenticates or resolves?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `AuthenticateExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the job id targeted (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Interrupt `job id targeted` between the writes.
- Invariant to test: record creation must be atomic with its credential
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no record exists without its credential
