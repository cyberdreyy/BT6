# Q1428: claim used for identity is attacker-settable in user.NewUser

## Question
Is the claim mapped to the node account by `NewUser` at POST /sessions and PATCH /v2/user/password one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an unauthenticated HTTP client that can reach the node API port collide with an operator account?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `NewUser`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: email string (unicode, case, whitespace) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `email string (unicode, case, whitespace)` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
