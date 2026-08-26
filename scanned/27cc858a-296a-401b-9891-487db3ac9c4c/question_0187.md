# Q0187: secret returned in the success response in jobs_controller.Index

## Question
Does the response produced by `Index` at POST/PATCH /v2/jobs (edit role) include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Index`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: bridge names and external job id (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `bridge names and external job id` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
