# Q0334: session not invalidated on logout in sync.NewLDAPServerStateSyncer

## Question
Does the session id used by an authenticated node user holding only the 'view' role at any authenticated /v2 request after LDAP group membership is revoked remain accepted by `NewLDAPServerStateSyncer` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `timing between group revocation and the sync tick` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
