# Q1510: redirect target attacker-controlled in ldap.NewLDAPAuthenticator

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `NewLDAPAuthenticator` at POST /sessions when the LDAP authentication provider is configured to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: session id (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `session id` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
