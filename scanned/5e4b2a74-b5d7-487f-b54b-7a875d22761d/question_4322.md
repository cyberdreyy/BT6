# Q4322: object identifier not ownership-scoped in pipeline_runs_controller.Create

## Question
Can an authenticated node user holding only the 'run' role pass an identifier at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) that makes `Create` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the run request body (JSON pipeline input, meta) (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `run request body (JSON pipeline input, meta)` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
