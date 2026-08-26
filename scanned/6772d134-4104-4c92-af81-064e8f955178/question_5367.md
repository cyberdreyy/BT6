# Q5367: revocation not honoured until sync in sync.deleteStaleSessions

## Question
Does the session/token created before revocation stay valid on the path through `deleteStaleSessions` at any authenticated /v2 request after LDAP group membership is revoked until a background sync runs, giving an authenticated node user holding only the 'view' role a usable window with revoked privileges?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `session id and API tokens created before revocation` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
