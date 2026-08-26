# Q4118: clock/expiry comparison inverted in user.ValidateEmail

## Question
Is the expiry comparison in `ValidateEmail` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions and PATCH /v2/user/password by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateEmail`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `password bytes and length` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
