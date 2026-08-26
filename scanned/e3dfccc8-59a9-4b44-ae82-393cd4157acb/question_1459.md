# Q1459: spec presenter echoes credentials in user.NewUserResources

## Question
Does the spec rendered by `NewUserResources` at the JSON:API response of GET /v2/users and /sessions include embedded credentials (bridge tokens, URLs with basic auth, initiator secrets, webhook tokens) submitted at creation time?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: which resource fields are serialized (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create an object with a credential-bearing field then fetch `which resource fields are serialized`.
- Invariant to test: credential-bearing spec fields must be redacted on read
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test creating with credentials and asserting redaction on read
