# Q5300: authorization oracle via response differences in gql.GetGQLAuthenticatedSession

## Question
Do the headers/status produced by `GetGQLAuthenticatedSession` differ enough between 'no such object' and 'forbidden' on POST /query (GraphQL) guarded by AuthenticateGQL to let an authenticated node user holding only the 'view' role enumerate protected objects before escalating?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `GetGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the session cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `session cookie` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
