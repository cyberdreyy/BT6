# Q1905: token lookup ignores scope in webauthn_controller.NewWebAuthnController

## Question
Does the API token lookup performed by `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) return a user without checking the token's owner, expiry or state, letting an authenticated node user holding only the 'view' role present a deleted user's token?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `registration attestation payload` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
