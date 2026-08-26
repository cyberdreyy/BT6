# Q1585: session cookie attributes in webauthn.BeginWebAuthnRegistration

## Question
Are the cookie attributes set around `BeginWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an unauthenticated HTTP client that can reach the node API port can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: the WebAuthn credential/assertion JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `WebAuthn credential/assertion JSON` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
