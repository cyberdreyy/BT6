# Q0337: session not invalidated on logout in webauthn_controller.NewWebAuthnController

## Question
Does the session id used by an authenticated node user holding only the 'view' role at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) remain accepted by `NewWebAuthnController` after logout, password change or role downgrade?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `webauthn session store cookie` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
