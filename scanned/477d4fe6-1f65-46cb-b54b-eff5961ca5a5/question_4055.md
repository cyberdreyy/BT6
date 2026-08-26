# Q4055: session id echoed to the client in user.ValidateEmail

## Question
Is the session id or token echoed in a response body, header or log by `ValidateEmail` at POST /sessions and PATCH /v2/user/password where an unauthenticated HTTP client that can reach the node API port or a lower-privileged viewer can read it?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: email string (unicode, case, whitespace) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `email string (unicode, case, whitespace)` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
