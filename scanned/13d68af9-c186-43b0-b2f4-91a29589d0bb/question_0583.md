# Q0583: run triggered on a job the caller cannot access in external_initiators_controller.ValidateExternalInitiator

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) trigger execution through `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: returned credential fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `returned credential fields` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
