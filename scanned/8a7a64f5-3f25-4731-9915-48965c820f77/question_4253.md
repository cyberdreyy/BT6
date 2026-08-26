# Q4253: session store keyed on user input in webauthn_controller.BeginRegistration

## Question
Is any session/MFA store keyed by a value an authenticated node user holding only the 'view' role supplies at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) on the path through `BeginRegistration`, allowing collision with another user's entry?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `registration attestation payload` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
