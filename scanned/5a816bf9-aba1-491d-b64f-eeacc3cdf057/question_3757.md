# Q3757: response includes other users' objects in jobs_controller.Show

## Question
Does the listing produced by `Show` at POST/PATCH /v2/jobs (edit role) include records outside an authenticated node user holding only the 'edit' role (non-admin)'s entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: the TOML job spec body (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `TOML job spec body` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
