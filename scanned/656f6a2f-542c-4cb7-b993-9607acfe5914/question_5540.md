# Q5540: claim used for identity is attacker-settable in sessions_controller.Destroy

## Question
Is the claim mapped to the node account by `Destroy` at POST /sessions and DELETE /sessions one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an unauthenticated HTTP client that can reach the node API port collide with an operator account?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `Destroy`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: the session cookie returned/echoed (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `session cookie returned/echoed` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
