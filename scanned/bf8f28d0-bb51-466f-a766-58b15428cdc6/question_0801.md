# Q0801: WebAuthn registration bound to the wrong user in session.NewSession

## Question
Can an unauthenticated HTTP client that can reach the node API port register a credential through `NewSession` at POST /sessions (session creation) and API-token authentication that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `WebAuthn data field` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
