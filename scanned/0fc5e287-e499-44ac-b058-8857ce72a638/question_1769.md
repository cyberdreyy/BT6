# Q1769: response includes other users' objects in log_controller.Patch

## Question
Does the listing produced by `Patch` at GET and PATCH /v2/log include records outside an authenticated node user holding only the 'view' role's entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: logLevel and sqlEnabled fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `logLevel and sqlEnabled fields` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
