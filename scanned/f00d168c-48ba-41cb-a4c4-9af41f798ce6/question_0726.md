# Q0726: WebAuthn assertion not bound to the challenge in reaper.NewSessionReaper

## Question
Can an authenticated node user holding only the 'view' role replay or forge the assertion validated by `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `repeated reuse of an old session id` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
