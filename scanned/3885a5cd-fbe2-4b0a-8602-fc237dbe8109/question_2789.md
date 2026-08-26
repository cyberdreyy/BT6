# Q2789: stale session past expiry in client.CreateEphemeralConnection

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a session alive indefinitely through the last-used update in `CreateEphemeralConnection` at POST /sessions against the configured LDAP server, so a stolen or shared session never expires?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: bind DN template inputs (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `bind DN template inputs` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
