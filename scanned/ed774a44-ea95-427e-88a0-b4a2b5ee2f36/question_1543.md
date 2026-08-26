# Q1543: cache serves a stale or foreign bridge in external_initiator.AuthenticateExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair exploit the cache in `AuthenticateExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs so a deleted, disabled or replaced bridge keeps being used, or a name collision returns another owner's bridge?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `AuthenticateExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the job id targeted (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Race `job id targeted` against the refresh interval.
- Invariant to test: the cache must be invalidated transactionally with the write and keyed unambiguously
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrency test mutating a bridge while reads run
