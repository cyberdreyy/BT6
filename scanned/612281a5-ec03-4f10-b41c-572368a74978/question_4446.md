# Q4446: export password not enforced in pipeline_runs_controller.Create

## Question
Can an authenticated node user holding only the 'run' role export key material through `Create` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) with an empty, default or attacker-chosen password, obtaining a bundle that is trivially decryptable offline?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Call `repeated/concurrent submissions` with an empty/weak password parameter.
- Invariant to test: export must require the caller's authenticated proof and a strong password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test exporting with an empty password and asserting failure
