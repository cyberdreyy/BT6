# Q1963: session fixation in cookies.FindSessionCookie

## Question
Does the session id observed on the path through `FindSessionCookie` survive privilege changes at the Cookie header on any authenticated /v2 route, letting an unauthenticated HTTP client that can reach the node API port pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `cookie name casing and attributes` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
