# Q0724: WebAuthn assertion not bound to the challenge in webauthn.BeginWebAuthnRegistration

## Question
Can an unauthenticated HTTP client that can reach the node API port replay or forge the assertion validated by `BeginWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: session store cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `session store cookie` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
