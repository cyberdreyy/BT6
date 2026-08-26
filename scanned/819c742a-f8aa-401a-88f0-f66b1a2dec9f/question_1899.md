# Q1899: token lookup ignores scope in reaper.NewSessionReaper

## Question
Does the API token lookup performed by `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change return a user without checking the token's owner, expiry or state, letting an authenticated node user holding only the 'view' role present a deleted user's token?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `timing of requests relative to session/token lifetime` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
