# Q1303: export bundle rendered to a non-owner in user.NewUserResources

## Question
Does `NewUserResources` render exported key material at the JSON:API response of GET /v2/users and /sessions to any caller passing the role gate rather than the key owner/admin only?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: which resource fields are serialized (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `which resource fields are serialized` from the weakest role accepted.
- Invariant to test: export material may only be rendered to an admin-authenticated owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test requesting the export from each role
