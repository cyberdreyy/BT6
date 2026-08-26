# Q0594: run triggered on a job the caller cannot access in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role trigger execution through `Patch` at GET and PATCH /v2/log for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated toggling of SQL logging` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
