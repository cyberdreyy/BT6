# Q1670: login race creates duplicate identity in sessions_controller.NewSessionsController

## Question
Can concurrent requests to POST /sessions and DELETE /sessions racing inside `NewSessionsController` create two sessions or two user rows for one identity, so an unauthenticated HTTP client that can reach the node API port keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: email, password and WebAuthn fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `email, password and WebAuthn fields`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
