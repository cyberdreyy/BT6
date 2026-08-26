# Q2341: token deletion does not revoke in webauthn_controller.NewWebAuthnController

## Question
Does deleting an API token or session through `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) leave it usable in a cache or replica, so an authenticated node user holding only the 'view' role's revoked credential still authenticates?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `registration attestation payload` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
