# Q0573: API token minted for another identity in sessions_controller.NewSessionsController

## Question
Can an unauthenticated HTTP client that can reach the node API port cause `NewSessionsController` at POST /sessions and DELETE /sessions to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: the session cookie returned/echoed (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session cookie returned/echoed` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
