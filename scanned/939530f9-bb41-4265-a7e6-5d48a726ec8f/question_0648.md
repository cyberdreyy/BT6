# Q0648: role attribute taken from the request in ldap.NewLDAPAuthenticator

## Question
Does the account/role creation path through `NewLDAPAuthenticator` at POST /sessions when the LDAP authentication provider is configured accept the role from an unauthenticated HTTP client that can reach the node API port's payload rather than from server policy?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: email/username string (LDAP metacharacters) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `email/username string (LDAP metacharacters)` with an elevated role field in the create/update body.
- Invariant to test: role assignment must be server-controlled and require admin authority
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test posting a role field from a low-role session
