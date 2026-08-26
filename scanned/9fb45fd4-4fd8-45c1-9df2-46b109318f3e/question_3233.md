# Q3233: directory metacharacter injection in identity lookup in webauthn.FinishWebAuthnRegistration

## Question
Can an unauthenticated HTTP client that can reach the node API port inject filter metacharacters through `FinishWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `FinishWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: the WebAuthn credential/assertion JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `WebAuthn credential/assertion JSON` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
