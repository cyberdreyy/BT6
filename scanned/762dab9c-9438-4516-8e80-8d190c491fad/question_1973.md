# Q1973: session id echoed to the client in ldap.NewLDAPAuthenticator

## Question
Is the session id or token echoed in a response body, header or log by `NewLDAPAuthenticator` at POST /sessions when the LDAP authentication provider is configured where an unauthenticated HTTP client that can reach the node API port or a lower-privileged viewer can read it?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: password bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `password bytes` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
