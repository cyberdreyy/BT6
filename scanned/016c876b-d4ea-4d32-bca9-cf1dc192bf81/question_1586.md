# Q1586: session cookie attributes in orm.NewORM

## Question
Are the cookie attributes set around `NewORM` at POST /sessions, API-token auth headers and session cookie lookup weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an unauthenticated HTTP client that can reach the node API port can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `NewORM`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: email casing/whitespace (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `email casing/whitespace` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
