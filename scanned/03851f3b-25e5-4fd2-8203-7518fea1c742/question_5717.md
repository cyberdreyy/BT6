# Q5717: error text discloses key or file paths in pipeline_runs_controller.Create

## Question
Do errors from `Create` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'run' role target the next step of a key-theft chain?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the run request body (JSON pipeline input, meta) (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `run request body (JSON pipeline input, meta)`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
