# Q2191: session store keyed on user input in reaper.NewSessionReaper

## Question
Is any session/MFA store keyed by a value an authenticated node user holding only the 'view' role supplies at any authenticated /v2 request made after logout, password change or role change on the path through `NewSessionReaper`, allowing collision with another user's entry?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of requests relative to session/token lifetime` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
