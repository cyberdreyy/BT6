# Q1693: resource type confusion in user.NewUserResources

## Question
Can an authenticated node user holding only the 'view' role cause `NewUserResources` at the JSON:API response of GET /v2/users and /sessions to render one resource type with another's attribute set, exposing fields the intended presenter would redact?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: the authenticated identity requesting the list (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `authenticated identity requesting the list` with a mismatched type/id.
- Invariant to test: the presenter selected must match the object type exactly
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over presenter selection for mismatched types
