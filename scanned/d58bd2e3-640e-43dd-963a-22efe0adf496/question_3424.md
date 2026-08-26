# Q3424: revocation not honoured until sync in user.ValidateEmail

## Question
Does the session/token created before revocation stay valid on the path through `ValidateEmail` at POST /sessions and PATCH /v2/user/password until a background sync runs, giving an unauthenticated HTTP client that can reach the node API port a usable window with revoked privileges?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: role string submitted (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `role string submitted` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
