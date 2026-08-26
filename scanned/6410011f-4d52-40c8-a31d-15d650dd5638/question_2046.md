# Q2046: clock/expiry comparison inverted in ldap.NewLDAPAuthenticator

## Question
Is the expiry comparison in `NewLDAPAuthenticator` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions when the LDAP authentication provider is configured by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: group membership values returned for the DN (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `group membership values returned for the DN` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
