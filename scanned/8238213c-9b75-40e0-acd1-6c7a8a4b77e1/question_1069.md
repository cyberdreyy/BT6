# Q1069: struct embedding pulls in secret fields in user.NewUserResources

## Question
Does `NewUserResources` embed a domain struct so newly added secret fields are serialized automatically at the JSON:API response of GET /v2/users and /sessions without anyone reviewing the response shape?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: the authenticated identity requesting the list (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `authenticated identity requesting the list` and compare fields against the intended resource contract.
- Invariant to test: presenters must copy explicit fields rather than embed domain structs
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting the presenter's field set equals an explicit allowlist
