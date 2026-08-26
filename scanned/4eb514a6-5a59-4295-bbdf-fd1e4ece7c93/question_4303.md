# Q4303: stale role after change in helpers.paginatedResponse

## Question
Does a session or token validated through `paginatedResponse` keep its old role at the JSON:API response writer used by every /v2 controller after the role was downgraded or the user deleted, letting an authenticated node user holding only the 'view' role act with revoked privileges?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `requested resource type` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
