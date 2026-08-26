# Q4617: session token generation entropy in webauthn_controller.FinishRegistration

## Question
Is the session id or API token produced on the path through `FinishRegistration` derived from a predictable source (time, counter, weak RNG), letting an authenticated node user holding only the 'view' role predict a token issued to an admin and replay it at POST /v2/users/webauthn (BeginRegistration/FinishRegistration)?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `FinishRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Collect many issued values via `registration attestation payload` and test for structure.
- Invariant to test: session ids and API tokens must come from a CSPRNG with full entropy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: statistical test over many generated tokens plus a code path review of the RNG source
