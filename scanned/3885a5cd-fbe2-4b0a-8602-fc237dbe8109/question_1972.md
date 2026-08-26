# Q1972: session id echoed to the client in reaper.NewSessionReaper

## Question
Is the session id or token echoed in a response body, header or log by `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change where an authenticated node user holding only the 'view' role or a lower-privileged viewer can read it?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `repeated reuse of an old session id` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
