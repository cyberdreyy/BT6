# Q2051: clock/expiry comparison inverted in webauthn_controller.NewWebAuthnController

## Question
Is the expiry comparison in `NewWebAuthnController` inverted or evaluated against the wrong field, so an expired session or token presented at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) by an authenticated node user holding only the 'view' role still authenticates?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `credential id and user handle` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
