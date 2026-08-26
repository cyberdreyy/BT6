# Q4725: session not invalidated on logout in authentication.AuthenticationProvider

## Question
Does the session id used by an unauthenticated HTTP client that can reach the node API port at POST /sessions and every AuthenticationProvider call behind /v2 auth remain accepted by `AuthenticationProvider` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProvider`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: submitted email and password (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `submitted email and password` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
