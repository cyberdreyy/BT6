# Q0257: token compared without constant time in sessions_controller.NewSessionsController

## Question
Does the secret comparison used by `NewSessionsController` at POST /sessions and DELETE /sessions leak byte position through timing or early return, letting an unauthenticated HTTP client that can reach the node API port recover an admin API secret?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: email, password and WebAuthn fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `email, password and WebAuthn fields`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
