# Q2470: email canonicalization mismatch in sync.Work

## Question
Can an authenticated node user holding only the 'view' role authenticate through `Work` at any authenticated /v2 request after LDAP group membership is revoked as an existing operator by submitting an email that differs in case, unicode normalization or trailing whitespace from the stored one, so lookup succeeds against a different record than the one whose password is checked?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing between group revocation and the sync tick` in a variant form and compare the record found by lookup with the record whose hash is verified.
- Invariant to test: the identity looked up and the identity whose secret is verified must be the same row
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the user lookup with case/unicode/whitespace email variants
