# Q1147: custom marshaller leaks on error in user.NewUserResources

## Question
Does the marshalling path around `NewUserResources` fall back to default struct marshalling on error at the JSON:API response of GET /v2/users and /sessions, exposing unredacted fields to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/user.go](core/web/presenters/user.go) -> `NewUserResources`
- Entrypoint: the JSON:API response of GET /v2/users and /sessions
- Attacker controls: which resource fields are serialized (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch via `which resource fields are serialized`.
- Invariant to test: marshalling failure must produce an error, never a raw dump
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test forcing marshal errors and asserting no raw payload
