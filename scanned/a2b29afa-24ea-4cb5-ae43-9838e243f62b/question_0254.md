# Q0254: token compared without constant time in client.newLDAPClient

## Question
Does the secret comparison used by `newLDAPClient` at POST /sessions against the configured LDAP server leak byte position through timing or early return, letting an unauthenticated HTTP client that can reach the node API port recover an admin API secret?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `username and password fields`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
