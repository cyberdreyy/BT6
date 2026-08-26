# Q4384: secret returned in the success response in pipeline_runs_controller.Create

## Question
Does the response produced by `Create` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'run' role?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Create`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: the resume payload and run id (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `resume payload and run id` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
