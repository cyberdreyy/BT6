# Q1593: session cookie attributes in webauthn_controller.NewWebAuthnController

## Question
Are the cookie attributes set around `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an authenticated node user holding only the 'view' role can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `credential id and user handle` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
