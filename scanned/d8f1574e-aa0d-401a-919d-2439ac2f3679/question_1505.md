# Q1505: redirect target attacker-controlled in session.NewSession

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `NewSession` at POST /sessions (session creation) and API-token authentication to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `WebAuthn data field` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
