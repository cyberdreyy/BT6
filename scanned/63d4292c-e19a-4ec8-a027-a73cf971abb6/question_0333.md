# Q0333: session not invalidated on logout in client.newLDAPClient

## Question
Does the session id used by an unauthenticated HTTP client that can reach the node API port at POST /sessions against the configured LDAP server remain accepted by `newLDAPClient` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: bind DN template inputs (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `bind DN template inputs` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
