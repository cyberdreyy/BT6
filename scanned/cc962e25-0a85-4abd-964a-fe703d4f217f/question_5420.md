# Q5420: state parameter not verified in webauthn.BeginWebAuthnLogin

## Question
Is the state/nonce checked by `BeginWebAuthnLogin` at POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration unbound to the initiating browser session, letting an unauthenticated HTTP client that can reach the node API port inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/webauthn.go](core/sessions/webauthn.go) -> `BeginWebAuthnLogin`
- Entrypoint: POST /sessions with a WebAuthn assertion and /v2/users/webauthn registration
- Attacker controls: the WebAuthn credential/assertion JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `WebAuthn credential/assertion JSON` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
