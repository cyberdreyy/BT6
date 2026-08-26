# Q0438: identifier reveals sensitive identity in user.NewUserResource

## Question
Does the identifier or metadata rendered by `NewUserResource` at the JSON:API response of GET /v2/users and /sessions reveal key identities, addresses or credential fingerprints that let an authenticated node user holding only the 'view' role target key theft or fund movement?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResource`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: the authenticated identity requesting the list (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `authenticated identity requesting the list` at the lowest role.
- Invariant to test: identity metadata must be limited to what the caller's role needs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test comparing rendered identifiers per role
