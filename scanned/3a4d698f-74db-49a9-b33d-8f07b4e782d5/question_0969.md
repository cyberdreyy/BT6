# Q0969: directory metacharacter injection in identity lookup in webauthn_controller.NewWebAuthnController

## Question
Can an authenticated node user holding only the 'view' role inject filter metacharacters through `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `registration attestation payload` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
