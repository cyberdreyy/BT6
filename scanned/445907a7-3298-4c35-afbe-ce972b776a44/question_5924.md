# Q5924: session fixation in helpers.paginatedRequest

## Question
Does the session id observed on the path through `paginatedRequest` survive privilege changes at the JSON:API response writer used by every /v2 controller, letting an authenticated node user holding only the 'view' role pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `pagination parameters` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
