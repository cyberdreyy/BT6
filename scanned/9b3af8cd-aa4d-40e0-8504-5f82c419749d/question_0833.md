# Q0833: listing renders objects across owners in user.NewUserResource

## Question
Does the collection built by `NewUserResource` at the JSON:API response of GET /v2/users and /sessions render objects outside an authenticated node user holding only the 'view' role's entitlement together with their sensitive attributes?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResource`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: which resource fields are serialized (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `which resource fields are serialized` as a low-role user.
- Invariant to test: collections must be filtered before rendering
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing collection contents per role
