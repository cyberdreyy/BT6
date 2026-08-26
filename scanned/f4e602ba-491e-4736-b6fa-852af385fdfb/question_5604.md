# Q5604: validation performed on a copy in pipeline_runs_controller.Create

## Question
Does `Create` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) validate one representation of an authenticated node user holding only the 'run' role's input while persisting or executing another (re-parsed, re-serialized, defaulted), so the executed object escapes validation?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated/concurrent submissions` whose two parses differ (duplicate keys, aliases, unknown fields).
- Invariant to test: the validated bytes and the executed object must be the same value
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: differential test comparing validated and persisted structures
