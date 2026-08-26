# Q4616: session token generation entropy in sessions_controller.Destroy

## Question
Is the session id or API token produced on the path through `Destroy` derived from a predictable source (time, counter, weak RNG), letting an unauthenticated HTTP client that can reach the node API port predict a token issued to an admin and replay it at POST /sessions and DELETE /sessions?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `Destroy`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: email, password and WebAuthn fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Collect many issued values via `email, password and WebAuthn fields` and test for structure.
- Invariant to test: session ids and API tokens must come from a CSPRNG with full entropy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: statistical test over many generated tokens plus a code path review of the RNG source
