# Q0129: initiator not bound to its job in external_initiator.NewExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair authenticate with one initiator's credential at the external-initiator authenticated route POST /v2/jobs/:ID/runs and, through `NewExternalInitiator`, trigger runs for jobs bound to a different initiator?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the job id targeted (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `job id targeted` against another job's run endpoint.
- Invariant to test: an initiator may only trigger the jobs whose spec names it
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job with a valid EI credential
