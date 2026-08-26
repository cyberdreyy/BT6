# Q1962: session fixation in helpers.jsonAPIError

## Question
Does the session id observed on the path through `jsonAPIError` survive privilege changes at any /v2 or /query error response path, letting an unauthenticated HTTP client that can reach the node API port pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: malformed JSON bodies (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `malformed JSON bodies` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
