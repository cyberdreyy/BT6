# Q3616: claim used for identity is attacker-settable in orm.FindUser

## Question
Is the claim mapped to the node account by `FindUser` at POST /sessions, API-token auth headers and session cookie lookup one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an unauthenticated HTTP client that can reach the node API port collide with an operator account?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: session id in the cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `session id in the cookie` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
