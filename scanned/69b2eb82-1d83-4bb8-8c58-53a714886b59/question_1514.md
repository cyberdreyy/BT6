# Q1514: redirect target attacker-controlled in sessions_controller.NewSessionsController

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `NewSessionsController` at POST /sessions and DELETE /sessions to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: the session cookie returned/echoed (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `session cookie returned/echoed` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
