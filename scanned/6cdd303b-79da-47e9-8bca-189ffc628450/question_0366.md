# Q0366: bridge URL replaced under a live job in external_initiator.NewExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair update the bridge URL/token through `NewExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs so running jobs fetch observations from an attacker endpoint, changing the reported value?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the job id targeted (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Patch `job id targeted` to point at an attacker host.
- Invariant to test: bridge target changes must require admin authority and revalidate referencing jobs
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test patching a bridge used by a live job
