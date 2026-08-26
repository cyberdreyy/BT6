# Q3808: login race creates duplicate identity in client.CreateEphemeralConnection

## Question
Can concurrent requests to POST /sessions against the configured LDAP server racing inside `CreateEphemeralConnection` create two sessions or two user rows for one identity, so an unauthenticated HTTP client that can reach the node API port keeps a session that the operator cannot see or revoke?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `connection reuse across logins`.
- Invariant to test: session and user creation must be serialized and idempotent per identity
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: concurrent test asserting a single row/session results
