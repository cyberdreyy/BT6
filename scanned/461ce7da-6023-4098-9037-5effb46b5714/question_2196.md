# Q2196: session store keyed on user input in sessions_controller.NewSessionsController

## Question
Is any session/MFA store keyed by a value an unauthenticated HTTP client that can reach the node API port supplies at POST /sessions and DELETE /sessions on the path through `NewSessionsController`, allowing collision with another user's entry?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `NewSessionsController`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: the session cookie returned/echoed (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session cookie returned/echoed` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
