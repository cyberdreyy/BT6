# Q2040: clock/expiry comparison inverted in authentication.AuthenticationProviderName

## Question
Is the expiry comparison in `AuthenticationProviderName` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions and every AuthenticationProvider call behind /v2 auth by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProviderName`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: session id presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `session id presented` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
