# Q4248: session store keyed on user input in ldap.FindUser

## Question
Is any session/MFA store keyed by a value an unauthenticated HTTP client that can reach the node API port supplies at POST /sessions when the LDAP authentication provider is configured on the path through `FindUser`, allowing collision with another user's entry?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: email/username string (LDAP metacharacters) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `email/username string (LDAP metacharacters)` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
