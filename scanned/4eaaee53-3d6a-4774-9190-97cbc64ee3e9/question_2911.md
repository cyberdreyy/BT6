# Q2911: API token minted for another identity in session.GenerateAuthToken

## Question
Can an unauthenticated HTTP client that can reach the node API port cause `GenerateAuthToken` at POST /sessions (session creation) and API-token authentication to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `GenerateAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: email/password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `email/password fields` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
