# Q0487: password change without old-password proof in webauthn.BeginWebAuthnRegistration

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `BeginWebAuthnRegistration` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnRegistration`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: registration challenge response (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `registration challenge response` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
