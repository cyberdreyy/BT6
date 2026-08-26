# Q2119: privileged bootstrap account reachable in ldap.NewLDAPAuthenticator

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions when the LDAP authentication provider is configured through `NewLDAPAuthenticator` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: session id (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `session id` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
