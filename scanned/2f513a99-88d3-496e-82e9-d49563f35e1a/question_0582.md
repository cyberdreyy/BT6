# Q0582: run triggered on a job the caller cannot access in jobs_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) trigger execution through `Index` at POST/PATCH /v2/jobs (edit role) for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Index`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update payload on an existing job` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
