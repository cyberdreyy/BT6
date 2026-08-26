# Q0574: API token minted for another identity in webauthn_controller.NewWebAuthnController

## Question
Can an authenticated node user holding only the 'view' role cause `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `webauthn session store cookie` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
