# Q4050: session fixation in helpers.addForbiddenErrorHeaders

## Question
Does the session id observed on the path through `addForbiddenErrorHeaders` survive privilege changes at any /v2 or /query error response path, letting an unauthenticated HTTP client that can reach the node API port pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `inputs that force an error branch` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
