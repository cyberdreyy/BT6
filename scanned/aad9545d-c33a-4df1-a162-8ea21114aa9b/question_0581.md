# Q0581: run triggered on a job the caller cannot access in pipeline_runs_controller.Index

## Question
Can an authenticated node user holding only the 'run' role trigger execution through `Index` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Index`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated/concurrent submissions` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
