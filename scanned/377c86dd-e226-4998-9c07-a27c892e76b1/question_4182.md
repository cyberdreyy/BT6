# Q4182: privileged bootstrap account reachable in webauthn.FinishWebAuthnRegistration

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration through `FinishWebAuthnRegistration` as a bootstrap/default account that remains enabled with a derivable credential?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: credential id and user handle (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Try `credential id and user handle` against default/bootstrap identities.
- Invariant to test: no account may exist with a credential derivable from public information
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting bootstrap accounts require an explicitly set secret
