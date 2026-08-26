# Q0966: directory metacharacter injection in identity lookup in sync.NewLDAPServerStateSyncer

## Question
Can an authenticated node user holding only the 'view' role inject filter metacharacters through `NewLDAPServerStateSyncer` at any authenticated /v2 request after LDAP group membership is revoked so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing between group revocation and the sync tick` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
