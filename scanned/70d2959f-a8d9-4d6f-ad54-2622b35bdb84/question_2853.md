# Q2853: password change without old-password proof in client.CreateEphemeralConnection

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `CreateEphemeralConnection` at POST /sessions against the configured LDAP server without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `connection reuse across logins` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
