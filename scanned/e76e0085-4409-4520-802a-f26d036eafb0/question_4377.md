# Q4377: token deletion does not revoke in webauthn_controller.BeginRegistration

## Question
Does deleting an API token or session through `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) leave it usable in a cache or replica, so an authenticated node user holding only the 'view' role's revoked credential still authenticates?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `credential id and user handle` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
