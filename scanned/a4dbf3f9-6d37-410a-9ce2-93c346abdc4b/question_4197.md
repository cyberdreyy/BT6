# Q4197: read route exposes a write-only field in pipeline_runs_controller.Show

## Question
Does the read path through `Show` at POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential) return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'run' role?

## Target
- File/function: [core/web/pipeline_runs_controller.go](core/web/pipeline_runs_controller.go) -> `Show`
- Entrypoint: POST /v2/jobs/:ID/runs and PATCH /v2/resume/:runID (run role or external-initiator credential)
- Attacker controls: repeated/concurrent submissions (attacker capability: an authenticated node user holding only the 'run' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `repeated/concurrent submissions` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
