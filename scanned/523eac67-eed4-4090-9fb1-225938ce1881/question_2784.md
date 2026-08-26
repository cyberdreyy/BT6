# Q2784: stale session past expiry in user.ValidateEmail

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a session alive indefinitely through the last-used update in `ValidateEmail` at POST /sessions and PATCH /v2/user/password, so a stolen or shared session never expires?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `password bytes and length` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
