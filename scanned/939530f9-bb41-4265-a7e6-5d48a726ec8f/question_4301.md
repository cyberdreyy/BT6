# Q4301: stale role after change in helpers.addForbiddenErrorHeaders

## Question
Does a session or token validated through `addForbiddenErrorHeaders` keep its old role at any /v2 or /query error response path after the role was downgraded or the user deleted, letting an unauthenticated HTTP client that can reach the node API port act with revoked privileges?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: malformed JSON bodies (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `malformed JSON bodies` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
