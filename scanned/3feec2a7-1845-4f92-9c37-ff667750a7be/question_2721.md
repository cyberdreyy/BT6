# Q2721: session not invalidated on logout in webauthn.FinishWebAuthnRegistration

## Question
Does the session id used by an unauthenticated HTTP client that can reach the node API port at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration remain accepted by `FinishWebAuthnRegistration` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: the WebAuthn credential/assertion JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `WebAuthn credential/assertion JSON` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
