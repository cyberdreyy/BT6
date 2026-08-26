# Q2041: clock/expiry comparison inverted in session.NewSession

## Question
Is the expiry comparison in `NewSession` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions (session creation) and API-token authentication by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `supplied access key and secret` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
