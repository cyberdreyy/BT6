# Q3930: password hash parameters or algorithm downgrade in webauthn.FinishWebAuthnRegistration

## Question
Can an unauthenticated HTTP client that can reach the node API port cause the verification in `FinishWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration to accept a hash produced with a weaker algorithm/cost stored in the record, enabling offline recovery of an admin password?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: credential id and user handle (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare verification behaviour for `credential id and user handle` across stored hash formats.
- Invariant to test: only the current algorithm and cost may be accepted for verification
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the verifier with legacy hash formats
