# Q1465: bridge URL replaced under a live job in external_initiator.AuthenticateExternalInitiator

## Question
Can a holder of an external-initiator access-key/secret pair update the bridge URL/token through `AuthenticateExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs so running jobs fetch observations from an attacker endpoint, changing the reported value?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `AuthenticateExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the accessKey/secret headers (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Patch `accessKey/secret headers` to point at an attacker host.
- Invariant to test: bridge target changes must require admin authority and revalidate referencing jobs
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test patching a bridge used by a live job
