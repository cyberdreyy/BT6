# Q2043: clock/expiry comparison inverted in webauthn.BeginWebAuthnRegistration

## Question
Is the expiry comparison in `BeginWebAuthnRegistration` inverted or evaluated against the wrong field, so an expired session or token presented at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration by an unauthenticated HTTP client that can reach the node API port still authenticates?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: registration challenge response (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `registration challenge response` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
