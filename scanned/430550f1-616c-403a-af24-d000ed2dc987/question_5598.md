# Q5598: redirect target attacker-controlled in webauthn_controller.FinishRegistration

## Question
Can an authenticated node user holding only the 'view' role steer the post-authentication redirect handled near `FinishRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `FinishRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `credential id and user handle` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
