# Q2665: token compared without constant time in webauthn_controller.BeginRegistration

## Question
Does the secret comparison used by `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) leak byte position through timing or early return, letting an authenticated node user holding only the 'view' role recover an admin API secret?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: credential id and user handle (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `credential id and user handle`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
