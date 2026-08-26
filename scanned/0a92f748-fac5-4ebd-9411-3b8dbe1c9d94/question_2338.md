# Q2338: token deletion does not revoke in sync.NewLDAPServerStateSyncer

## Question
Does deleting an API token or session through `NewLDAPServerStateSyncer` at any authenticated /v2 request after LDAP group membership is revoked leave it usable in a cache or replica, so an authenticated node user holding only the 'view' role's revoked credential still authenticates?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `timing between group revocation and the sync tick` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
