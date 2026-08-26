# Q3997: token lookup ignores scope in client.CreateEphemeralConnection

## Question
Does the API token lookup performed by `CreateEphemeralConnection` at POST /sessions against the configured LDAP server return a user without checking the token's owner, expiry or state, letting an unauthenticated HTTP client that can reach the node API port present a deleted user's token?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `connection reuse across logins` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
