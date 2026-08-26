# Q1849: secret in relationship/included documents in user.NewUserResources

## Question
Does the JSON:API relationship or included section produced around `NewUserResources` at the JSON:API response of GET /v2/users and /sessions carry secret attributes of related objects to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: the authenticated identity requesting the list (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `authenticated identity requesting the list` with include parameters.
- Invariant to test: included resources must be redacted like primary resources
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting included documents pass the same redaction
