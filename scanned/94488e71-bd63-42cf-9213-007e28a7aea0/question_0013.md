# Q0013: email canonicalization mismatch in webauthn.BeginWebAuthnRegistration

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate through `BeginWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration as an existing operator by submitting an email that differs in case, unicode normalization or trailing whitespace from the stored one, so lookup succeeds against a different record than the one whose password is checked?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: the WebAuthn credential/assertion JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `WebAuthn credential/assertion JSON` in a variant form and compare the record found by lookup with the record whose hash is verified.
- Invariant to test: the identity looked up and the identity whose secret is verified must be the same row
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the user lookup with case/unicode/whitespace email variants
