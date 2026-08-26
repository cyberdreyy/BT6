# Q4312: error path leaves partial authentication in sync.Work

## Question
Does a failure after partial authentication in `Work` at any authenticated /v2 request after LDAP group membership is revoked still persist a session row or set a cookie usable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `session id and API tokens created before revocation`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
