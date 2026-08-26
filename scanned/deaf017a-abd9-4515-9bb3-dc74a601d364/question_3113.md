# Q3113: WebAuthn registration bound to the wrong user in webauthn_controller.BeginRegistration

## Question
Can an authenticated node user holding only the 'view' role register a credential through `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `registration attestation payload` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
