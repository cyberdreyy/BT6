# Q1281: state parameter not verified in webauthn_controller.NewWebAuthnController

## Question
Is the state/nonce checked by `NewWebAuthnController` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) unbound to the initiating browser session, letting an authenticated node user holding only the 'view' role inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `NewWebAuthnController`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `webauthn session store cookie` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
