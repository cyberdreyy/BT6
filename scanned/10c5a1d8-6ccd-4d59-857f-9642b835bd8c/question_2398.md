# Q2398: identity provider failure fails open in session.NewSession

## Question
If the external identity backend behind `NewSession` is unreachable, does POST /sessions (session creation) and API-token authentication fall back to a permissive path that authenticates an unauthenticated HTTP client that can reach the node API port or maps them to a default role?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the failure while submitting `WebAuthn data field`.
- Invariant to test: backend failure must fail closed with no role assignment
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test injecting backend errors and asserting a 401 with no session
