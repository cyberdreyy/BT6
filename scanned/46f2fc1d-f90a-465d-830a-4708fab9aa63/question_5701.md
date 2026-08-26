# Q5701: login race creates duplicate identity in authentication.AuthenticationProvider

## Question
Can concurrent requests to POST /sessions and every AuthenticationProvider call behind /v2 auth racing inside `AuthenticationProvider` create two sessions or two user rows for one identity, so an unauthenticated HTTP client that can reach the node API port keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProvider`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: API token pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `API token pair`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
