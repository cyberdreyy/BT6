# Q2929: resume/callback path unauthenticated or unbound in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) resume or complete a pending run through `Show` at POST/PATCH /v2/jobs (edit role) by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update payload on an existing job` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
