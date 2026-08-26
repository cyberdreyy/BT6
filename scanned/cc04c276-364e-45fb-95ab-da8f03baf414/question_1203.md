# Q1203: revocation not honoured until sync in webauthn_controller.NewWebAuthnController

## Question
Does the session/token created before revocation stay valid on the path through `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) until a background sync runs, giving an authenticated node user holding only the 'view' role a usable window with revoked privileges?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `registration attestation payload` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
