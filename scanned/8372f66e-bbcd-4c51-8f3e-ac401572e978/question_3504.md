# Q3504: identifier-to-object confusion across types in pipeline_runs_controller.Show

## Question
Can an authenticated node user holding only the 'run' role supply an identifier of the wrong type/namespace at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) so `Show` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Show`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the job ID/external job ID in the path (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `job ID/external job ID in the path` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
