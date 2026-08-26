# Q2255: stale role after change in api.ParsePaginatedRequest

## Question
Does a session or token validated through `ParsePaginatedRequest` keep its old role at page/size query parameters on /v2 index endpoints after the role was downgraded or the user deleted, letting an authenticated node user holding only the 'view' role act with revoked privileges?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `JSON:API document fields in the request body` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
