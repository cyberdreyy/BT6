# Q2728: session not invalidated on logout in sessions_controller.Create

## Question
Does the session id used by an unauthenticated HTTP client that can reach the node API port at POST /sessions and DELETE /sessions remain accepted by `Create` after logout, password change or role downgrade?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `Create`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: email, password and WebAuthn fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `email, password and WebAuthn fields` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
