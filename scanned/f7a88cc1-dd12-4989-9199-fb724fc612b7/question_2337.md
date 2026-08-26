# Q2337: token deletion does not revoke in client.newLDAPClient

## Question
Does deleting an API token or session through `newLDAPClient` at POST /sessions against the configured LDAP server leave it usable in a cache or replica, so an unauthenticated HTTP client that can reach the node API port's revoked credential still authenticates?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `username and password fields` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
