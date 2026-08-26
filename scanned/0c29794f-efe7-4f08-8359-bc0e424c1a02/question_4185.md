# Q4185: privileged bootstrap account reachable in ldap.FindUser

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions when the LDAP authentication provider is configured through `FindUser` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: session id (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `session id` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
