# Q1351: token claims trusted without verification in webauthn.BeginWebAuthnRegistration

## Question
Does the identity token processed by `BeginWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration get accepted with unverified signature, issuer, audience or expiry, letting an unauthenticated HTTP client that can reach the node API port present a self-issued token and become an admin?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: session store cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `session store cookie` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
