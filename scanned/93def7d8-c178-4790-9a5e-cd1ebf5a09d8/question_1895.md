# Q1895: token lookup ignores scope in session.NewSession

## Question
Does the API token lookup performed by `NewSession` at POST /sessions (session creation) and API-token authentication return a user without checking the token's owner, expiry or state, letting an unauthenticated HTTP client that can reach the node API port present a deleted user's token?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: email/password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `email/password fields` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
