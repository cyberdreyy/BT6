# Q4369: token deletion does not revoke in webauthn.FinishWebAuthnRegistration

## Question
Does deleting an API token or session through `FinishWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration leave it usable in a cache or replica, so an unauthenticated HTTP client that can reach the node API port's revoked credential still authenticates?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: registration challenge response (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `registration challenge response` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
