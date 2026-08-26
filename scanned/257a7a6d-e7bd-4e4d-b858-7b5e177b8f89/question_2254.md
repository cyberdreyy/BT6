# Q2254: stale role after change in cookies.FindSessionCookie

## Question
Does a session or token validated through `FindSessionCookie` keep its old role at the Cookie header on any authenticated /v2 route after the role was downgraded or the user deleted, letting an unauthenticated HTTP client that can reach the node API port act with revoked privileges?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `cookie value encoding` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
