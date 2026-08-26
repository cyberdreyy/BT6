# Q4051: session fixation in api.paginationLink

## Question
Does the session id observed on the path through `paginationLink` survive privilege changes at page/size query parameters on /v2 index endpoints, letting an authenticated node user holding only the 'view' role pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `page and size query values` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
