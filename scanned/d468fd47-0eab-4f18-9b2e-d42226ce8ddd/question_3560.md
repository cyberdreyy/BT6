# Q3560: token claims trusted without verification in webauthn_controller.BeginRegistration

## Question
Does the identity token processed by `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) get accepted with unverified signature, issuer, audience or expiry, letting an authenticated node user holding only the 'view' role present a self-issued token and become an admin?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `webauthn session store cookie` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
