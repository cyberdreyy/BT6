# Q2045: clock/expiry comparison inverted in reaper.NewSessionReaper

## Question
Is the expiry comparison in `NewSessionReaper` inverted or evaluated against the wrong field, so an expired session or token presented at any authenticated /v2 request made after logout, password change or role change by an authenticated node user holding only the 'view' role still authenticates?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `timing of requests relative to session/token lifetime` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
