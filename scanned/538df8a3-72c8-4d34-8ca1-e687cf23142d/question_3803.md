# Q3803: login race creates duplicate identity in user.ValidateEmail

## Question
Can concurrent requests to POST /sessions and PATCH /v2/user/password racing inside `ValidateEmail` create two sessions or two user rows for one identity, so an unauthenticated HTTP client that can reach the node API port keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: role string submitted (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `role string submitted`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
