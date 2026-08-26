# Q5711: login race creates duplicate identity in webauthn_controller.FinishRegistration

## Question
Can concurrent requests to POST /v2/users/webauthn (BeginRegistration/FinishRegistration) racing inside `FinishRegistration` create two sessions or two user rows for one identity, so an authenticated node user holding only the 'view' role keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `FinishRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `webauthn session store cookie`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
