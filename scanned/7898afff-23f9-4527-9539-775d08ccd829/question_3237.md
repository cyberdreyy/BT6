# Q3237: directory metacharacter injection in identity lookup in client.CreateEphemeralConnection

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `CreateEphemeralConnection` at POST /sessions against the configured LDAP server so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `connection reuse across logins` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
