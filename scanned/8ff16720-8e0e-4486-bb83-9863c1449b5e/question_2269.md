# Q2269: error path leaves partial authentication in webauthn_controller.NewWebAuthnController

## Question
Does a failure after partial authentication in `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) still persist a session row or set a cookie usable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `credential id and user handle`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
