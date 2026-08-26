# Q4198: read route exposes a write-only field in jobs_controller.Show

## Question
Does the read path through `Show` at POST/PATCH /v2/jobs (edit role) return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `update payload on an existing job` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
