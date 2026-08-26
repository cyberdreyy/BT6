# Q1592: session cookie attributes in sessions_controller.NewSessionsController

## Question
Are the cookie attributes set around `NewSessionsController` at POST /sessions and DELETE /sessions weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an unauthenticated HTTP client that can reach the node API port can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: repeated concurrent login attempts (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `repeated concurrent login attempts` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
