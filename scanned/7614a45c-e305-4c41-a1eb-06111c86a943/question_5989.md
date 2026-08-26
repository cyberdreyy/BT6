# Q5989: clock/expiry comparison inverted in oidc.handleCheckEnabled

## Question
Is the expiry comparison in `handleCheckEnabled` inverted or evaluated against the wrong field, so an expired session or token presented at the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/oidcauth/oidc.go](core/sessions/oidcauth/oidc.go) -> `handleCheckEnabled`
- Entrypoint: the OIDC sign-in and token-exchange HTTP endpoints exposed when OIDC auth is enabled
- Attacker controls: ID token claims presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `ID token claims presented` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
