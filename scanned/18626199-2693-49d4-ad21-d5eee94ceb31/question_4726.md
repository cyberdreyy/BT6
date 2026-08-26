# Q4726: session not invalidated on logout in session.SetAuthToken

## Question
Does the session id used by an unauthenticated HTTP client that can reach the node API port at POST /sessions (session creation) and API-token authentication remain accepted by `SetAuthToken` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `SetAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `supplied access key and secret` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
