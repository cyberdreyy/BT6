# Q5310: unauthenticated bind treated as success in sync.deleteStaleSessions

## Question
Can an authenticated node user holding only the 'view' role authenticate at any authenticated /v2 request after LDAP group membership is revoked through `deleteStaleSessions` by submitting an empty password so the directory performs an unauthenticated bind that the code reads as success?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing between group revocation and the sync tick` with an empty or whitespace password.
- Invariant to test: empty-password binds must be rejected before contacting the directory
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/space passwords asserting rejection
