# Q5548: response includes other users' objects in jobs_controller.Create

## Question
Does the listing produced by `Create` at POST/PATCH /v2/jobs (edit role) include records outside an authenticated node user holding only the 'edit' role (non-admin)'s entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Create`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: bridge names and external job id (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `bridge names and external job id` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
