# Q3806: login race creates duplicate identity in reaper.Work

## Question
Can concurrent requests to any authenticated /v2 request made after logout, password change or role change racing inside `Work` create two sessions or two user rows for one identity, so an authenticated node user holding only the 'view' role keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `Work`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `repeated reuse of an old session id`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
