# Q2793: stale session past expiry in webauthn_controller.BeginRegistration

## Question
Can an authenticated node user holding only the 'view' role keep a session alive indefinitely through the last-used update in `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration), so a stolen or shared session never expires?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `webauthn session store cookie` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
