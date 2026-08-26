# Q0653: role attribute taken from the request in webauthn_controller.NewWebAuthnController

## Question
Does the account/role creation path through `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) accept the role from an authenticated node user holding only the 'view' role's payload rather than from server policy?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `credential id and user handle` with an elevated role field in the create/update body.
- Invariant to test: role assignment must be server-controlled and require admin authority
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test posting a role field from a low-role session
