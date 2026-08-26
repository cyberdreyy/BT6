# Q2917: API token minted for another identity in client.CreateEphemeralConnection

## Question
Can an unauthenticated HTTP client that can reach the node API port cause `CreateEphemeralConnection` at POST /sessions against the configured LDAP server to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `username and password fields` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
