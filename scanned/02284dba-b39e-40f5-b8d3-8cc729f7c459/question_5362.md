# Q5362: revocation not honoured until sync in user.ValidateAndHashPassword

## Question
Does the session/token created before revocation stay valid on the path through `ValidateAndHashPassword` at POST /sessions and PATCH /v2/user/password until a background sync runs, giving an unauthenticated HTTP client that can reach the node API port a usable window with revoked privileges?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `password bytes and length` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
