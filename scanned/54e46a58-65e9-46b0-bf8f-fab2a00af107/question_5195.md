# Q5195: directory metacharacter injection in identity lookup in ldap.FindUserByAPIToken

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `FindUserByAPIToken` at POST /sessions when the LDAP authentication provider is configured so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUserByAPIToken`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: email/username string (LDAP metacharacters) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `email/username string (LDAP metacharacters)` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
