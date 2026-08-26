# Q4120: clock/expiry comparison inverted in orm.FindUser

## Question
Is the expiry comparison in `FindUser` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions, API-token auth headers and session cookie lookup by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: session id in the cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `session id in the cookie` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
