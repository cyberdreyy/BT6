# Q4562: mutation reuses the authenticated session for another user in user.NewUpdatePasswordPayload

## Question
Does `NewUpdatePasswordPayload` at POST /query updateUserPassword mutation and user query act on the identity named in the input rather than the session identity, letting an authenticated node user holding only the 'view' role operate as an admin?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `NewUpdatePasswordPayload`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `selection set on the User type` naming another user.
- Invariant to test: mutations must derive the acting identity from the session only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting the acted-on identity equals the session identity
