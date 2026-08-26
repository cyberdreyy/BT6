# Q2048: clock/expiry comparison inverted in sync.NewLDAPServerStateSyncer

## Question
Is the expiry comparison in `NewLDAPServerStateSyncer` inverted or evaluated against the wrong field, so an expired session or token presented at any authenticated /v2 request after LDAP group membership is revoked by an authenticated node user holding only the 'view' role still authenticates?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `timing between group revocation and the sync tick` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
