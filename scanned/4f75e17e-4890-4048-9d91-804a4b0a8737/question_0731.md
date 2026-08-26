# Q0731: WebAuthn assertion not bound to the challenge in sessions_controller.NewSessionsController

## Question
Can an unauthenticated HTTP client that can reach the node API port replay or forge the assertion validated by `NewSessionsController` at POST /sessions and DELETE /sessions because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: email, password and WebAuthn fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `email, password and WebAuthn fields` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
