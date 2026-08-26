# Q3177: MFA store cookie forgeable in webauthn_controller.BeginRegistration

## Question
Is the WebAuthn session-store cookie handled around `BeginRegistration` unauthenticated or unsigned, letting an authenticated node user holding only the 'view' role craft one at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) to complete an MFA step for another user?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `webauthn session store cookie` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
