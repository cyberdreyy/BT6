# Q4730: session not invalidated on logout in reaper.deleteStaleSessions

## Question
Does the session id used by an authenticated node user holding only the 'view' role at any authenticated /v2 request made after logout, password change or role change remain accepted by `deleteStaleSessions` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `timing of requests relative to session/token lifetime` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
