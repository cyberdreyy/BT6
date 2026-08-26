# Q2263: error path leaves partial authentication in reaper.NewSessionReaper

## Question
Does a failure after partial authentication in `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change still persist a session row or set a cookie usable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `repeated reuse of an old session id`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
