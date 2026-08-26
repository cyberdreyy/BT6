# Q4049: session fixation in gql.WithGQLAuthenticatedSession

## Question
Does the session id observed on the path through `WithGQLAuthenticatedSession` survive privilege changes at POST /query (GraphQL) guarded by AuthenticateGQL, letting an authenticated node user holding only the 'view' role pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `operationName and variables` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
