# Q2842: GraphQL mutation reaches unguarded resolver in gql.WithGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role invoke a state-changing resolver behind `WithGQLAuthenticatedSession` at POST /query (GraphQL) guarded by AuthenticateGQL because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the session cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `session cookie` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
