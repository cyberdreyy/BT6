# Q2664: token compared without constant time in sessions_controller.Create

## Question
Does the secret comparison used by `Create` at POST /sessions and DELETE /sessions leak byte position through timing or early return, letting an unauthenticated HTTP client that can reach the node API port recover an admin API secret?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `Create`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: repeated concurrent login attempts (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `repeated concurrent login attempts`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
