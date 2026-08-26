# Q5361: revocation not honoured until sync in session.SetAuthToken

## Question
Does the session/token created before revocation stay valid on the path through `SetAuthToken` at POST /sessions (session creation) and API-token authentication until a background sync runs, giving an unauthenticated HTTP client that can reach the node API port a usable window with revoked privileges?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `SetAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `WebAuthn data field` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
