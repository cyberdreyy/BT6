# Q3623: claim used for identity is attacker-settable in webauthn_controller.BeginRegistration

## Question
Is the claim mapped to the node account by `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an authenticated node user holding only the 'view' role collide with an operator account?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `credential id and user handle` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
