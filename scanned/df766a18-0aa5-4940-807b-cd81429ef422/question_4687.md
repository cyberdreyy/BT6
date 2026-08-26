# Q4687: run triggered on a job the caller cannot access in keys_controller.Delete

## Question
Can an authenticated node user holding only the 'view' role trigger execution through `Delete` at /v2/keys/:keyType Index/Export/Import/Delete routes for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Delete`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the export password query parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password query parameter` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
