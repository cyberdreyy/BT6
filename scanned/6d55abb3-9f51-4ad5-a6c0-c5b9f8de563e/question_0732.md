# Q0732: WebAuthn assertion not bound to the challenge in webauthn_controller.NewWebAuthnController

## Question
Can an authenticated node user holding only the 'view' role replay or forge the assertion validated by `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `registration attestation payload` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
