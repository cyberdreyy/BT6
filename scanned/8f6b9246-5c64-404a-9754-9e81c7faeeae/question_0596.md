# Q0596: error/status fields carry raw upstream output in user.NewUserResource

## Question
Does `NewUserResource` include raw upstream errors or task results at the JSON:API response of GET /v2/users and /sessions that contain secrets or internal endpoints readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResource`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: the authenticated identity requesting the list (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger a failing run then fetch `authenticated identity requesting the list`.
- Invariant to test: rendered errors must be sanitized
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting rendered error fields are sanitized
