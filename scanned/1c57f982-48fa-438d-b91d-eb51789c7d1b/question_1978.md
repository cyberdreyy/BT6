# Q1978: session id echoed to the client in webauthn_controller.NewWebAuthnController

## Question
Is the session id or token echoed in a response body, header or log by `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) where an authenticated node user holding only the 'view' role or a lower-privileged viewer can read it?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `webauthn session store cookie` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
