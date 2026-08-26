# Q4245: session store keyed on user input in webauthn.FinishWebAuthnRegistration

## Question
Is any session/MFA store keyed by a value an unauthenticated HTTP client that can reach the node API port supplies at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration on the path through `FinishWebAuthnRegistration`, allowing collision with another user's entry?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: the WebAuthn credential/assertion JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `WebAuthn credential/assertion JSON` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
