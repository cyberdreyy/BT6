# Q2047: clock/expiry comparison inverted in client.newLDAPClient

## Question
Is the expiry comparison in `newLDAPClient` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions against the configured LDAP server by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `connection reuse across logins` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
