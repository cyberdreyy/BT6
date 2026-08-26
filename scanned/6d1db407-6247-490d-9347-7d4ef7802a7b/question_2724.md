# Q2724: session not invalidated on logout in ldap.FindUser

## Question
Does the session id used by an unauthenticated HTTP client that can reach the node API port at POST /sessions when the LDAP authentication provider is configured remain accepted by `FindUser` after logout, password change or role downgrade?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: email/username string (LDAP metacharacters) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Reuse `email/username string (LDAP metacharacters)` after each of those events.
- Invariant to test: any credential-changing event must invalidate all existing sessions and tokens
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test reusing a session id after logout/password change
