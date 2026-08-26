# Q2050: clock/expiry comparison inverted in sessions_controller.NewSessionsController

## Question
Is the expiry comparison in `NewSessionsController` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions and DELETE /sessions by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: repeated concurrent login attempts (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `repeated concurrent login attempts` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
