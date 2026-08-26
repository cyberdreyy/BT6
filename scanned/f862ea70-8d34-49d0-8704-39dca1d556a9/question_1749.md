# Q1749: user enumeration then targeted attack in webauthn_controller.NewWebAuthnController

## Question
Do responses from `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) distinguish unknown accounts from wrong passwords precisely enough for an authenticated node user holding only the 'view' role to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `webauthn session store cookie` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
