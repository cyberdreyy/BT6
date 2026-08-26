# Q1431: claim used for identity is attacker-settable in reaper.NewSessionReaper

## Question
Is the claim mapped to the node account by `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an authenticated node user holding only the 'view' role collide with an operator account?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `timing of requests relative to session/token lifetime` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
