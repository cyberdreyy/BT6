# Q1197: revocation not honoured until sync in reaper.NewSessionReaper

## Question
Does the session/token created before revocation stay valid on the path through `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change until a background sync runs, giving an authenticated node user holding only the 'view' role a usable window with revoked privileges?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `repeated reuse of an old session id` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
