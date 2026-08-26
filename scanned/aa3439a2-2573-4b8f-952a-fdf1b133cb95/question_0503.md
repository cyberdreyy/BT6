# Q0503: spec fields reach outbound requests with node credentials in jobs_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) point a URL/host field accepted by `Index` at POST/PATCH /v2/jobs (edit role) at an internal address or attacker host so the node performs a request carrying its own credentials or secrets?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Index`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: bridge names and external job id (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge names and external job id` with an internal or attacker URL.
- Invariant to test: outbound targets from user-supplied specs must be validated and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the URL validator with internal/attacker targets
