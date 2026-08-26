# Q0255: token compared without constant time in sync.NewLDAPServerStateSyncer

## Question
Does the secret comparison used by `NewLDAPServerStateSyncer` at any authenticated /v2 request after LDAP group membership is revoked leak byte position through timing or early return, letting an authenticated node user holding only the 'view' role recover an admin API secret?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `session id and API tokens created before revocation`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
