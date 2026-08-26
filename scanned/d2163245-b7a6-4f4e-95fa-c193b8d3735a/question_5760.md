# Q5760: user enumeration then targeted attack in webauthn.BeginWebAuthnLogin

## Question
Do responses from `BeginWebAuthnLogin` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration distinguish unknown accounts from wrong passwords precisely enough for an unauthenticated HTTP client that can reach the node API port to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnLogin`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: registration challenge response (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `registration challenge response` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
