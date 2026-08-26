# Q5926: session id echoed to the client in session.SetAuthToken

## Question
Is the session id or token echoed in a response body, header or log by `SetAuthToken` at POST /sessions (session creation) and API-token authentication where an unauthenticated HTTP client that can reach the node API port or a lower-privileged viewer can read it?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `SetAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `supplied access key and secret` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
