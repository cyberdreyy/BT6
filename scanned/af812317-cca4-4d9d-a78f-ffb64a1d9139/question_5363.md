# Q5363: revocation not honoured until sync in webauthn.BeginWebAuthnLogin

## Question
Does the session/token created before revocation stay valid on the path through `BeginWebAuthnLogin` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration until a background sync runs, giving an unauthenticated HTTP client that can reach the node API port a usable window with revoked privileges?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnLogin`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: credential id and user handle (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `credential id and user handle` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
