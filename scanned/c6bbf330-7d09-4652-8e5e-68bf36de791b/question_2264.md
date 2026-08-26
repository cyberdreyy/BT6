# Q2264: error path leaves partial authentication in ldap.NewLDAPAuthenticator

## Question
Does a failure after partial authentication in `NewLDAPAuthenticator` at POST /sessions when the LDAP authentication provider is configured still persist a session row or set a cookie usable by an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: password bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `password bytes`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
