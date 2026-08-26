# Q2408: identity provider failure fails open in webauthn_controller.NewWebAuthnController

## Question
If the external identity backend behind `NewWebAuthnController` is unreachable, does POST /v2/users/webauthn (BeginRegistration/FinishRegistration) fall back to a permissive path that authenticates an authenticated node user holding only the 'view' role or maps them to a default role?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the failure while submitting `webauthn session store cookie`.
- Invariant to test: backend failure must fail closed with no role assignment
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test injecting backend errors and asserting a 401 with no session
