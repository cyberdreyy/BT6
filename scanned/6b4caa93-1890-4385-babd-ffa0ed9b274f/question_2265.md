# Q2265: error path leaves partial authentication in client.newLDAPClient

## Question
Does a failure after partial authentication in `newLDAPClient` at POST /sessions against the configured LDAP server still persist a session row or set a cookie usable by an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `connection reuse across logins`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
