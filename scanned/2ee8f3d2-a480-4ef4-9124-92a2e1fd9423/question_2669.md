# Q2669: resolver executes before auth on error in user.Email

## Question
Does `Email` at POST /query updateUserPassword mutation and user query perform its side effect before its role assertion returns, so an authenticated node user holding only the 'view' role still causes the change while receiving an authorization error?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `Email`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `selection set on the User type` and inspect state afterwards.
- Invariant to test: authorization must complete before any side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting no state change accompanies an authorization error
