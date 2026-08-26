# Q0567: API token minted for another identity in orm.NewORM

## Question
Can an unauthenticated HTTP client that can reach the node API port cause `NewORM` at POST /sessions, API-token auth headers and session cookie lookup to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `NewORM`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: access key/secret pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `access key/secret pair` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
