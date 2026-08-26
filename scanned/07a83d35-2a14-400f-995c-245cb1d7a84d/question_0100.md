# Q0100: password verification skipped on missing user in webauthn_controller.NewWebAuthnController

## Question
Does `NewWebAuthnController` skip or short-circuit hash verification when the user row is absent or has an empty password hash, letting an authenticated node user holding only the 'view' role authenticate at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) against a non-existent or partially provisioned account?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `webauthn session store cookie` for an unknown or externally-managed account.
- Invariant to test: authentication must fail closed and always perform a full hash comparison
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test asserting a constant-time failure for unknown users and empty hashes
