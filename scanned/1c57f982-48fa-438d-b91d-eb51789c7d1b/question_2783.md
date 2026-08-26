# Q2783: stale session past expiry in session.GenerateAuthToken

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a session alive indefinitely through the last-used update in `GenerateAuthToken` at POST /sessions (session creation) and API-token authentication, so a stolen or shared session never expires?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `GenerateAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `WebAuthn data field` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
