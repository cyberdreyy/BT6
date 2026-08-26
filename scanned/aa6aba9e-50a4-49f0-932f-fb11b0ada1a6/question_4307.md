# Q4307: error path leaves partial authentication in webauthn.FinishWebAuthnRegistration

## Question
Does a failure after partial authentication in `FinishWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration still persist a session row or set a cookie usable by an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: session store cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `session store cookie`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
