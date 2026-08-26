# Q0280: redaction applied only on one route in user.NewUserResource

## Question
Is redaction in `NewUserResource` applied on the index route but not on show/export/create at the JSON:API response of GET /v2/users and /sessions, letting an authenticated node user holding only the 'view' role read the secret through the other route?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResource`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: the authenticated identity requesting the list (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare `authenticated identity requesting the list` across all routes rendering the same resource.
- Invariant to test: redaction must be a property of the resource, not of one route
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test comparing the field set across routes
