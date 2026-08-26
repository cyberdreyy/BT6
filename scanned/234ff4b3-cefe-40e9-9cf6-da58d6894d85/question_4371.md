# Q4371: token deletion does not revoke in reaper.Work

## Question
Does deleting an API token or session through `Work` at any authenticated /v2 request made after logout, password change or role change leave it usable in a cache or replica, so an authenticated node user holding only the 'view' role's revoked credential still authenticates?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `Work`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `timing of requests relative to session/token lifetime` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
