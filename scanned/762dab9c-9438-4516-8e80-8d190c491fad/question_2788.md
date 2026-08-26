# Q2788: stale session past expiry in ldap.FindUser

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a session alive indefinitely through the last-used update in `FindUser` at POST /sessions when the LDAP authentication provider is configured, so a stolen or shared session never expires?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: password bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `password bytes` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
