# Q2124: privileged bootstrap account reachable in webauthn_controller.NewWebAuthnController

## Question
Can an authenticated node user holding only the 'view' role authenticate at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) through `NewWebAuthnController` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `registration attestation payload` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
