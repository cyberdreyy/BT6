# Q0017: email canonicalization mismatch in client.newLDAPClient

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate through `newLDAPClient` at POST /sessions against the configured LDAP server as an existing operator by submitting an email that differs in case, unicode normalization or trailing whitespace from the stored one, so lookup succeeds against a different record than the one whose password is checked?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `newLDAPClient`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `username and password fields` in a variant form and compare the record found by lookup with the record whose hash is verified.
- Invariant to test: the identity looked up and the identity whose secret is verified must be the same row
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the user lookup with case/unicode/whitespace email variants
