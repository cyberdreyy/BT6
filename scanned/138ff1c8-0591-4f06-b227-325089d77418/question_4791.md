# Q4791: stale session past expiry in sync.deleteStaleSessions

## Question
Can an authenticated node user holding only the 'view' role keep a session alive indefinitely through the last-used update in `deleteStaleSessions` at any authenticated /v2 request after LDAP group membership is revoked, so a stolen or shared session never expires?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `session id and API tokens created before revocation` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
