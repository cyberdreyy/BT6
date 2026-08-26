# Q3693: deletion enables silent takeover in pipeline_runs_controller.Show

## Question
Can an authenticated node user holding only the 'run' role delete or disable an object through `Show` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) and recreate it with attacker-controlled contents under the same name, so existing jobs silently use it?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Show`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Delete then recreate via `repeated/concurrent submissions`.
- Invariant to test: recreation must not inherit references from a deleted object without revalidation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test deleting and recreating a referenced object
