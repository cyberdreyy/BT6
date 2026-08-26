# Q1739: user enumeration then targeted attack in session.NewSession

## Question
Do responses from `NewSession` at POST /sessions (session creation) and API-token authentication distinguish unknown accounts from wrong passwords precisely enough for an unauthenticated HTTP client that can reach the node API port to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `WebAuthn data field` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
