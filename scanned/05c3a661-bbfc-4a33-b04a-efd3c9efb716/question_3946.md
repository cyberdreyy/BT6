# Q3946: error text discloses key or file paths in jobs_controller.Show

## Question
Do errors from `Show` at POST/PATCH /v2/jobs (edit role) reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'edit' role (non-admin) target the next step of a key-theft chain?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `update payload on an existing job`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
