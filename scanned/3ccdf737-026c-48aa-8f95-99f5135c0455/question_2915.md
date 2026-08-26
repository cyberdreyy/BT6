# Q2915: API token minted for another identity in reaper.Work

## Question
Can an authenticated node user holding only the 'view' role cause `Work` at any authenticated /v2 request made after logout, password change or role change to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `Work`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated reuse of an old session id` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
