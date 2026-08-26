# Q0968: directory metacharacter injection in identity lookup in sessions_controller.NewSessionsController

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `NewSessionsController` at POST /sessions and DELETE /sessions so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: email, password and WebAuthn fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `email, password and WebAuthn fields` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
