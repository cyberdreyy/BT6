# Q1768: response includes other users' objects in config_controller.Show

## Question
Does the listing produced by `Show` at GET /v2/config/v2 include records outside an authenticated node user holding only the 'view' role's entitlement (other users, other initiators, other owners)?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `request path and query parameters` and compare returned ids to the caller's scope.
- Invariant to test: listings must be filtered by the caller's entitlement
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing listing contents across roles
