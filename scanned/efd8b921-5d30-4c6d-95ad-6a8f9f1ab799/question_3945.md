# Q3945: error text discloses key or file paths in pipeline_runs_controller.Show

## Question
Do errors from `Show` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'run' role target the next step of a key-theft chain?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Show`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `repeated/concurrent submissions`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
