# Q5655: session cookie attributes in webauthn_controller.FinishRegistration

## Question
Are the cookie attributes set around `FinishRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an authenticated node user holding only the 'view' role can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `FinishRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `registration attestation payload` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
