# Q1199: revocation not honoured until sync in client.newLDAPClient

## Question
Does the session/token created before revocation stay valid on the path through `newLDAPClient` at POST /sessions against the configured LDAP server until a background sync runs, giving an unauthenticated HTTP client that can reach the node API port a usable window with revoked privileges?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `username and password fields` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
