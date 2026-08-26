# Q2042: clock/expiry comparison inverted in user.NewUser

## Question
Is the expiry comparison in `NewUser` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions and PATCH /v2/user/password by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `NewUser`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: role string submitted (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `role string submitted` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
