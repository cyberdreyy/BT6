# Q4300: stale role after change in gql.WithGQLAuthenticatedSession

## Question
Does a session or token validated through `WithGQLAuthenticatedSession` keep its old role at POST /query (GraphQL) guarded by AuthenticateGQL after the role was downgraded or the user deleted, letting an authenticated node user holding only the 'view' role act with revoked privileges?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `operationName and variables` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
